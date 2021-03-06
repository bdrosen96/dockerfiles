#! /usr/bin/env python
# -*- coding: UTF-8 -*-

import os
import re
import glob
import urlparse
import datetime
import tarfile
import shutil

import rpm
import requests
import paramiko

from scp import SCPClient
from git import Repo

# Static
homedir = os.path.expanduser('~')
rpmbuilddir = homedir + '/rpmbuild'
specdir = rpmbuilddir + '/SPECS'
sourcedir = specdir
srpmsdir = rpmbuilddir + '/SRPMS'
rpmmacros_path = homedir + '/.rpmmacros'
packagecloud_config = homedir + '/.packagecloud'
tmpdir = '/tmp'
gitraw = "https://raw.githubusercontent.com"
github_prefix = "https://github.com/"

# Overridable vars
specfile = os.environ.get('SPECFILE')
sources = os.environ.get('SOURCES', '')
rpm_options = os.environ.get('RPM_OPTIONS', '')
distribution = os.environ.get('DISTRIBUTION', 'epel-7-x86_64')
specfile_tag = os.environ.get('SPECFILE_TAG')
upload_result = os.environ.get('UPLOAD_RESULT')
keyfile = os.environ.get('OIO_KEYFILE')
repo_name = os.environ.get('GIT_REPO_NAME', 'rpm-specfiles')
branch = os.environ.get('GIT_BRANCH', 'master')
gitremote = os.environ.get('GIT_REMOTE')
repo_ip = os.environ.get('OIO_REPO_IP')

gitaccount = 'open-io'
if gitremote:
  if gitremote.startswith(github_prefix):
    urlparsed = urlparse.urlparse(gitremote)
    gitaccount = urlparsed.path.split('/')[1]
  else:
    print 'Only urls starting with: ' + github_prefix + ' are supported'
    exit(1)

# If we're only given a package name, infer the corresponding github url
oio_package = os.environ.get('OIO_PACKAGE')
if not specfile and oio_package:
  specfile = "%s/%s/%s/%s/%s/%s.spec" % (gitraw, gitaccount, repo_name, branch, oio_package, oio_package)

def usage():
  print 'Usage: SPECFILE=http://example.com/myspecfile.spec build.py'

def splitext(path):
  '''Variant of os.path.splitext(path) that handles tar file extensions'''
  for ext in ['.tar.gz', '.tar.bz2', '.tar.xz']:
    if path.endswith(ext):
      return path[:-len(ext)], path[-len(ext):]
  return os.path.splitext(path)

def log_error(msg):
  print 'ERROR: ' + msg
  exit(1)

def log_info(msg):
  print 'INFO: ' + msg

def log(msg, level='INFO'):
  switch = {
    'INFO':  log_info,
    'ERR':   log_error,
    'ERROR': log_error,
  }
  try:
    switch.get(level)(msg)
  except Exception, e:
    log_error('Failed to log msg ' + msg)

rpmmacro_sign = """
%_signature gpg
%_gpg_name  vl@t.com
%_gpg_path  %(echo $HOME)/.gnupg
"""

def set_keyfile():
  if keyfile and os.path.exists(keyfile):
    ret = os.system('gpg --import ' + keyfile)
    if ret != 0:
      log("Failed to import the GPG private key, your packages won't be signed.")
      return False
    try:
      log('Setting %_signature')
      with open(rpmmacros_path, 'a') as rpmmacros:
        rpmmacros.write(rpmmacro_sign)
        return True
    except Exception, e:
      log('Failed to set macro %_signature')
      log(str(e), 'ERROR')
  return False

def set_sourcedir(srcdir=sourcedir, macros_path=rpmmacros_path):
  try:
    log('Setting %_sourcedir to ' + srcdir)
    with open(macros_path, 'w') as rpmmacros:
      rpmmacros.write('%_sourcedir ' + srcdir + '\n')
  except Exception, e:
    log('Failed to set macro %_sourcedir', 'ERROR')

def get_specfile():
  files = glob.glob(specdir + '/*.spec')
  if files:
    return files[0]
  return False

def is_git(url):
  return re.match('.*\\.git$', urlparse.urlparse(url).path)

def download_file(url, path):
  try:
    log('Downloading file ' + url)
    request = requests.get(url, timeout=10, stream=True)
    if request.status_code == 404:
      log('URL ' + url + ' not found.', 'ERROR')
    with open(path, 'wb') as fh:
      for chunk in request.iter_content(1024 * 1024):
        fh.write(chunk)
      fh.close()
  except Exception, e:
    log('Failed to download file ' + url, 'ERROR')

def url_strip_query_fragment(url):
  urlparsed = urlparse.urlparse(url)
  return urlparse.urlunsplit((urlparsed.scheme, urlparsed.netloc, urlparsed.path, '', ''))

def git_clone(url, destdir, branch='master', commit=None, clean=True, archive=None, arcname=None):
  try:
    log('Cloning git repository ' + url)
    urlparsed = urlparse.urlparse(url)
    wkdir = tmpdir + '/' + os.path.basename(urlparsed.path)
    repo = Repo.clone_from(url, wkdir, branch=branch)
  except Exception, e:
    log('Failed to clone git repository ' + url)
    log('Exception :' + str(e), 'ERROR')
  if commit:
    try:
      log('Resetting git repository to commid id ' + commit)
      repo.head.reset(commit=commit, index=True)
    except Exception, e:
      log('Failed to reset git repository to commit id ' + commit)
      log('Exception :' + str(e), 'ERROR')
  if clean:
    clean_git_repo(wkdir)
  if archive:
    if not arcname:
      arcname = splitext(archive)[0]
    create_archive(destdir + '/' + archive, wkdir, arcname)
  else:
    for filename in glob.glob(wkdir + '/*'):
      try:
        shutil.move(filename, destdir)
      except Exception, e:
        log('Failed to copy file ' + filename + ' to ' + destdir)
        log('Exception :' + str(e), 'ERROR')

def clean_git_repo(directory):
  try:
    shutil.rmtree(directory + '/.git')
    os.remove(directory + '/.gitignore')
  except Exception, e:
    log('Failed to remove git files in ' + directory)


def create_archive(archive, source, arcname=None, clean=True):
  try:
    compression = os.path.splitext(archive)[1][1:]
    if not arcname:
      arcname = splitext(archive)[0]
    log('Creating archive ' + archive + ' with compression ' + compression)
    with tarfile.open(archive, 'w:' + compression) as tar:
      tar.add(source, arcname=arcname)
  except Exception, e:
    log('Failed to create tar file ' + archive + ' from ' + source)
    log('Exception :' + str(e), 'ERROR')
  if clean:
    try:
      shutil.rmtree(source)
    except Exception, e:
      log('Failed to remove directory ' + source)

def set_specdir(spcdir=''):
  global specdir
  if spcdir:
    spcdir = '/' + spcdir
  new_specdir = rpmbuilddir + '/SPECS' + spcdir
  if specdir == sourcedir:
    set_sourcedir(new_specdir)
  log('Setting specfile workdir to ' + new_specdir)
  specdir = new_specdir

def download_specfile(url, directory):
  urlparsed = urlparse.urlparse(url)
  query = urlparse.parse_qs(urlparsed.query)
  stripped_url = url_strip_query_fragment(url)
  if query.get('branch'):
    branch = query['branch'][0]
  else:
    branch = 'master'
  if query.get('specdir'):
    set_specdir(query['specdir'][0])
  if is_git(stripped_url):
    git_clone(stripped_url, directory, branch)
  else:
    download_file(stripped_url, directory + '/' + os.path.basename(urlparsed.path))

def set_rpm_options():
  if specfile_tag:
    global rpm_options
    rpm_options = "--define '_with_test 1' --define 'tag " + specfile_tag + "'"

def get_rpmts():
  if specfile_tag:
    rpm.addMacro('_with_test', '1')
    rpm.addMacro('tag', specfile_tag)
  return rpm.TransactionSet()

def download_sources():
  if sources:
    sources_list = sources.split(' ')
    for i, url in enumerate(sources_list):
      if not url:
        continue
      urlparsed = urlparse.urlparse(url)
      query = urlparse.parse_qs(urlparsed.query)
      stripped_url = url_strip_query_fragment(url)
      if is_git(stripped_url):
        if query.get('branch'):
          branch = query['branch'][0]
        else:
          branch = 'master'
        if query.get('commit'):
          commit = query['commit'][0]
        else:
          commit = None
        spec = get_rpmts().parseSpec(get_specfile())
        specsource = spec.sourceHeader['SOURCE'][i]
        if query.get('arcname'):
          arcname = query['arcname'][0]
        else:
          arcname = splitext(os.path.basename(specsource))[0]
        git_clone(stripped_url, sourcedir, branch, commit, archive=os.path.basename(specsource), arcname=arcname)
      else:
        download_file(stripped_url, sourcedir + '/' + os.path.basename(urlparsed.path))

def spectool(rpm_options, specfile):
  ret = os.system('/usr/bin/spectool -g -S -R ' + rpm_options + ' ' + specfile)
  if ret != 0:
    log('Failed to get source files.', 'ERROR')

def rpmbuild_bs(rpm_options, specfile):
  ret = os.system('/usr/bin/rpmbuild -bs --nodeps ' + rpm_options + ' ' + specfile)
  if ret != 0:
    log('Failed to create SRPM package.', 'ERROR')

def get_repo_data():
  today = datetime.date.today()
  return {
      'company': os.environ.get('OIO_COMPANY', 'openio'),
      'prod': os.environ.get('OIO_PROD', 'sds'),
      'prod_ver': os.environ.get('OIO_PROD_VER', today.strftime("%y.%m")),
      'distro': os.environ.get('OIO_DISTRO', 'centos'),
      'distro_ver': os.environ.get('OIO_DISTRO_VER', '7'),
      'arch': os.environ.get('OIO_ARCH', 'x86_64'),
  }

def patch_mock_config(distribution):
  '''Replace the baseurl in the mock configuration file pointing to the openio
  mirror, so that it points to the currently being populated one.
  This allows mock to find newly built packages that are depended upon.
  '''
  mock_cfg = '/etc/mock/' + distribution + '.cfg'
  newlines = []
  with open(mock_cfg, 'rb') as fin:
    lines = fin.readlines()
    inopeniosection = False
    for line in lines:
      if line.startswith('baseurl=http://mirror.openio.io'):
        repodata = get_repo_data()
        repodata.update({'repo_ip': repo_ip, 'repo_port': '8080'})
        newlines.append('baseurl=http://%(repo_ip)s:%(repo_port)s/pub/repo/%(company)s/%(prod)s/%(prod_ver)s/%(distro)s/%(distro_ver)s/%(arch)s/\n' % repodata)
      else:
        newlines.append(line)
  if not os.path.exists('/home/builder/.config'):
    os.mkdir('/home/builder/.config')
  # Overrides global config with local one
  with open('/home/builder/.config/mock.cfg', 'wb') as fout:
    fout.writelines(newlines)

def mock(distribution, rpm_options, srpmsdir, upload_result):
  # FIXME: currently only doing this if uploading to oiorepo
  if urlparse.urlparse(upload_result).scheme == 'http':
    patch_mock_config(distribution)
  ret = os.system('/usr/bin/mock -r ' + distribution + ' ' + rpm_options + ' --rebuild ' + srpmsdir + '/*.src.rpm')
  if ret != 0:
    log('Failed to build packages.', 'ERROR')

def list_result():
  log('Listing generated files:')
  for path in glob.glob('/var/lib/mock/*/result/*.rpm'):
    log('- ' + path)

def sign_rpms():
  log('Signing generated files')
  cmd = ['rpmsign', '--addsign'] + glob.glob('/var/lib/mock/*/result/*.rpm')
  ret = os.system(' '.join(cmd))
  if ret != 0:
    log('Failed to sign packages.')

def upload(url):
  urlparsed = urlparse.urlparse(url)
  if urlparsed.scheme == 'scp':
    return upload_scp(url)
  if urlparsed.scheme == 'packagecloud':
    return upload_pc(url)
  if urlparsed.scheme == 'http':
    return upload_http(url)
  log('URL scheme ' + urlparsed.scheme + ' not supported.')
  return False


def upload_http(url):
  '''Upload packages to an oiorepo web application'''
  # http://127.0.0.1:5000/package
  log('Uploading files using HTTP')
  urlparsed = urlparse.urlparse(url)
  if urlparsed.scheme != 'http':
    log('Cannot upload files using http since URI seems not to be a http protocol', 'ERROR')

  for lpath in glob.glob('/var/lib/mock/*/result/*.rpm'):
    with open(lpath, "rb") as fin:
        files = {"file": fin}
        data = get_repo_data()
        ret = requests.post(url, files=files, data=data)
        if ret.status_code != requests.codes.ok:
          log('Cannot upload package: ' + os.path.basename(lpath))
          log('to oiorepo: ' + url)
          log('Parameters for oiorepo web app: ' + str(data))
          log('request.status_code: ' + str(ret.status_code))
          log('request.text:')
          log(ret.text)
          ret.raise_for_status()


def upload_scp(url):
  # scp://host/remote_path/?port=22&username=user&password=passwd || packagecloud://user/
  log('Uploading files using SCP')
  urlparsed = urlparse.urlparse(url)
  if urlparsed.scheme != 'scp':
    log('Cannot upload files using scp since URI seems not to be a scp protocol', 'ERROR')
  host = urlparsed.netloc
  rpath = urlparsed.path.lstrip('/')
  query = urlparse.parse_qs(urlparsed.query)
  if query.get('username'):
    user = query['username'][0]
  else:
    user = None
  if query.get('password'):
    passwd = query['password'][0]
  else:
    passwd = None
  if query.get('port'):
    port = query['port'][0]
  else:
    port = 22
  try:
    ssh = paramiko.SSHClient()
    ssh.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    ssh.load_system_host_keys()
    ssh.connect(hostname=host, port=port, username=user, password=passwd)
    scp = SCPClient(ssh.get_transport())
  except Exception, e:
    log('Failed to create connection to ' + host)
    log('Exception :' + str(e), 'ERROR')
  for lpath in glob.glob('/var/lib/mock/*/result/*.rpm'):
    try:
      scp.put(lpath, rpath)
    except Exception, e:
      log('Failed to upload file ' + lpath + ' to ' + host + ':' + rpath)
      log('Exception :' + str(e), 'ERROR')

def pc_config(token):
  try:
    log('Configure Package Cloud token')
    with open(packagecloud_config, 'w') as pc_config:
      pc_config.write(token)
    return True
  except Exception, e:
    log('Failed to set token  in ' + packagecloud_config, 'ERROR')

def upload_pc(url):
  # packagecloud://user/repo/distro/distro_server?token='{"url":"https://packagecloud.io","token":"763ba46554b1a31e1c9ab7a148a74440d43a22a7eb6112a9"}'
  urlparsed = urlparse.urlparse(url)
  query = urlparse.parse_qs(urlparsed.query)
  splitted_path = (urlparsed.path.strip('/')).split('/')
  user = urlparsed.netloc
  if len(splitted_path) == 3:
    repo, distro, distro_version = splitted_path
  if len(splitted_path) == 1:
    repo = splitted_path[0]
    distro, distro_version = mock2pc_dist()
  if query.get('token'):
    token = query['token'][0]
  if not 'token' in locals():
    log('Package Cloud upload required a token to push packages')
    return False
  if not pc_config(token):
    return False
  log('Uploading files to Package Cloud ' + user + '/' + repo + '/' + distro + '/' + distro_version)
  for lpath in glob.glob('/var/lib/mock/*/result/*.rpm'):
    try:
      log('Uploading file ' + lpath)
      ret = os.system('LANG=en_US.UTF-8 /usr/local/bin/package_cloud push ' + user + '/' + repo + '/' + distro + '/' + distro_version + ' ' + lpath)
      if ret != 0:
        log('Failed to upload package ' + lpath + ' to Package Cloud to ' + user + '/' + repo + '/' + distro + '/' + distro_version, 'ERROR')
    except Exception, e:
      log('Failed to push file ' + lpath + ' to Package Cloud ' + user + '/' + repo + '/' + distro + '/' + distro_version)
      log('Exception :' + str(e), 'ERROR')
  log('Upload to Package Cloud ended successfully.')

def mock2pc_dist():
  splitted_distribution = distribution.split('-')
  if len(splitted_distribution) == 4:
    dist, distvers, _, _ = splitted_distribution
  else:
    dist, distvers, _ = splitted_distribution
  if dist == 'epel':
    distro = 'el'
  else:
    distro = dist
  return distro, distvers

def main():
  set_rpm_options()
  set_specdir()
  set_sourcedir()
  key_ok = set_keyfile()
  # Download the specfile if not already present
  if not get_specfile():
    download_specfile(specfile, specdir)
  # Check and download files using spectool
  #download_sources(sources, sourcedir)
  download_sources()
  spectool(rpm_options, get_specfile())
  # Create the SRPM
  rpmbuild_bs(rpm_options, get_specfile())
  # Build the package
  mock(distribution, rpm_options, srpmsdir, upload_result)
  # List the resulting packages
  list_result()
  if key_ok:
    sign_rpms()
  if upload_result:
    upload(upload_result)


if __name__ == "__main__":
    main()
