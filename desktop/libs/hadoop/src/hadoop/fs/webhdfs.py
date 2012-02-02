#!/usr/bin/env python
# Licensed to Cloudera, Inc. under one
# or more contributor license agreements.  See the NOTICE file
# distributed with this work for additional information
# regarding copyright ownership.  Cloudera, Inc. licenses this file
# to you under the Apache License, Version 2.0 (the
# "License"); you may not use this file except in compliance
# with the License.  You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""
Interfaces for Hadoop filesystem access via HttpFs/WebHDFS
"""

import errno
import logging
import threading

from desktop.lib.rest import http_client, resource
from hadoop.fs import normpath, SEEK_SET, SEEK_CUR, SEEK_END
from hadoop.fs.hadoopfs import encode_fs_path, Hdfs
from hadoop.fs.exceptions import WebHdfsException
from hadoop.fs.webhdfs_types import WebHdfsStat, WebHdfsContentSummary


DEFAULT_USER = 'hue_webui'

# The number of bytes to read if not specified
DEFAULT_READ_SIZE = 1024*1024 # 1MB

LOG = logging.getLogger(__name__)

class WebHdfs(Hdfs):
  """
  WebHdfs implements the filesystem interface via the WebHDFS rest protocol.
  """
  def __init__(self, url,
               hdfs_superuser="hdfs",
               security_enabled=False,
               temp_dir="/tmp"):
    self._url = url
    self._superuser = hdfs_superuser
    self._security_enabled = security_enabled
    self._temp_dir = temp_dir

    self._client = self._make_client(url)
    self._root = resource.Resource(self._client)

    # To store user info
    self._thread_local = threading.local()
    self.setuser(DEFAULT_USER)

    LOG.debug("Initializing Hadoop WebHdfs: %s (security: %s, superuser: %s)" %
              
              
              S: %s (security: %s, superuser: %s)" %
              (self._url, self._security_enabled, self._superuser))

  @classmethod
  def from_config(cls, hdfs_config):
    return cls(url=_get_service_url(hdfs_config),
               security_enabled=hdfs_config.SECURITY_ENABLED.get(),
               temp_dir=hdfs_config.TEMP_DIR.get())

  def __str__(self):
    return "WebHdfs at %s" % (self._url,)

  def _make_client(self, url):
    return http_client.HttpClient(
        url, exc_class=WebHdfsException, logger=LOG)

  @property
  def uri(self):
    return self._url

  @property
  def superuser(self):
    return self._superuser
  
  @property
  def user(self):
    return self.thread_local

  def _getparams(self):
    return { "user.name" : self._thread_local.user }

  def setuser(self, user):
    self._thread_local.user = user


  def listdir_stats(self, path, glob=None):
    """
    listdir_stats(path, glob=None) -> [ WebHdfsStat ]

    Get directory listing with stats.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    if glob is not None:
      params['filter'] = glob
    params['op'] = 'LISTSTATUS'
    json = self._root.get(path, params)
    filestatus_list = json['FileStatuses']['FileStatus']
    return [ WebHdfsStat(st, path) for st in filestatus_list ]

  def listdir(self, path, glob=None):
    """
    listdir(path, glob=None) -> [ entry names ]

    Get directory entry names without stats.
    """
    dirents = self.listdir_stats(self, path, glob)
    return [ x.path for x in dirents ]

  def get_content_summary(self, path):
    """
    get_content_summary(path) -> WebHdfsContentSummary
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'GETCONTENTSUMMARY'
    json = self._root.get(path, params)
    return WebHdfsContentSummary(json['ContentSummary'])


  def _stats(self, path):
    """This version of stats returns None if the entry is not found"""
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'GETFILESTATUS'
    try:
      json = self._root.get(path, params)
      return WebHdfsStat(json['FileStatus'], path)
    except WebHdfsException, ex:
      if ex.server_exc == 'FileNotFoundException':
        return None
      raise ex

  def stats(self, path):
    """
    stats(path) -> WebHdfsStat
    """
    res = self._stats(path)
    if res is not None:
      return res
    raise IOError(errno.ENOENT, "File %s not found" % (path,))

  def exists(self, path):
    return self._stats(path) is not None

  def isdir(self, path):
    sb = self._stats(path)
    if sb is None:
      return False
    return sb.isDir

  def isfile(self, path):
    sb = self._stats(path)
    if sb is None:
      return False
    return not sb.isDir

  def _delete(self, path, recursive=False):
    """
    _delete(path, recursive=False)

    Delete a file or directory.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'DELETE'
    params['recursive'] = recursive and 'true' or 'false'
    result = self._root.delete(path, params)
    # This part of the API is nonsense.
    # The lack of exception should indicate success.
    if not result['boolean']:
      raise IOError('Delete failed: %s' % (path,))

  def remove(self, path):
    """Delete a file."""
    self._delete(path, recursive=False)

  def rmdir(self, path):
    """Delete a file."""
    self._delete(path, recursive=False)

  def rmtree(self, path):
    """Delete a tree recursively."""
    self._delete(path, recursive=True)

  def mkdir(self, path, mode=None):
    """
    mkdir(path, mode=None)

    Creates a directory and any parent directory if necessary.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'MKDIRS'
    if mode is not None:
      params['permission'] = safe_octal(mode)
    success = self._root.put(path, params)
    if not success:
      raise IOError("Mkdir failed: %s" % (path,))

  def rename(self, old, new):
    """rename(old, new)"""
    old = encode_fs_path(Hdfs.normpath(old))
    new = encode_fs_path(Hdfs.normpath(new))
    params = self._getparams()
    params['op'] = 'RENAME'
    params['destination'] = new
    result = self._root.put(old, params)
    if not result['boolean']:
      raise IOError("Rename failed: %s -> %s" % (old, new))

  def chown(self, path, user=None, group=None):
    """chown(path, user=None, group=None)"""
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'SETOWNER'
    if user is not None:
      params['owner'] = user
    if group is not None:
      params['group'] = group
    self._root.put(path, params)

  def chmod(self, path, mode):
    """chmod(path, mode)"""
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'SETPERMISSION'
    params['permission'] = safe_octal(mode)
    self._root.put(path, params)

  def get_home_dir(self):
    """get_home_dir() -> Home directory for the current user"""
    params = self._getparams()
    params['op'] = 'GETHOMEDIRECTORY'
    res = self._root.get(params=params)
    return res['Path']


  def read(self, path, offset, length, bufsize=None):
    """
    read(path, offset, length[, bufsize]) -> data

    Read data from a file.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'OPEN'
    params['offset'] = long(offset)
    params['length'] = long(length)
    if bufsize is not None:
      params['bufsize'] = bufsize
    return self._root.get_raw(path, params)

  def open(self, path, mode='r'):
    """
    DEPRECATED!
    open(path, mode='r') -> File object

    This exists for legacy support and backwards compatibility only.
    Please use read().
    """
    return File(self, path, mode)


  def create(self, path, overwrite=False, blocksize=None,
             replication=None, permission=None, data=None):
    """
    create(path, overwrite=False, blocksize=None, replication=None, permission=None)

    Creates a file with the specified parameters.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'CREATE'
    params['overwrite'] = overwrite and 'true' or 'false'
    if blocksize is not None:
      params['blocksize'] = long(blocksize)
    if replication is not None:
      params['replication'] = int(replication)
    if permission is not None:
      params['permission'] = safe_octal(permission)

    self._invoke_with_redirect('PUT', path, params, data)


  def append(self, path, data):
    """
    append(path, data)

    Append data to a given file.
    """
    path = encode_fs_path(Hdfs.normpath(path))
    params = self._getparams()
    params['op'] = 'APPEND'
    self._invoke_with_redirect('POST', path, params, data)


  def _invoke_with_redirect(self, method, path, params=None, data=None):
    """
    Issue a request, and expect a redirect, and then submit the data to
    the redirected location. This is used for create, write, etc.

    Returns the 
    """
    next_url = None
    try:
      # Do not pass data in the first leg.
      self._root.invoke(method, path, params)
    except WebHdfsException, ex:
      # This is expected. We get a 307 redirect.
      # The following call may throw.
      next_url = self._get_redirect_url(ex)

    if next_url is None:
      raise WebHdfsException(
        "Failed to create '%s'. HDFS did not return a redirect" % (path,))

    # Now talk to the real thing. The redirect url already includes the params.
    client = self._make_client(next_url)
    return resource.Resource(client).invoke(
        method, data=data, json_decode=False)


  def _get_redirect_url(self, webhdfs_ex):
    """Retrieve the redirect url from an exception object"""
    try:
      # The actual HttpError (307) is wrapped inside
      http_error = webhdfs_ex.get_parent_ex()
      if http_error is None:
        raise webhdfs_ex

      if http_error.code not in (301, 302, 303, 307):
        LOG.error("Response is not a redirect: %s" % (webhdfs_ex,))
        raise webhdfs_ex
      return http_error.headers.getheader('location')
    except Exception, ex:
      LOG.error("Failed to read redirect from response: %s (%s)" %
                (webhdfs_ex, ex))
      raise webhdfs_ex

  def get_delegation_token(self, renewer):
    """get_delegation_token(user) -> Delegation token"""
    params = self._getparams()
    params['op'] = 'GETDELEGATIONTOKEN'
    params['renewer'] = renewer
    res = self._root.get(params=params)
    return res['Token']['urlString']



class File(object):
  """
  DEPRECATED!

  Represent an open file on HDFS. This exists to mirror the old thriftfs
  interface, for backwards compatibility only.
  """
  def __init__(self, fs, path, mode='r'):
    self._fs = fs
    self._path = normpath(path)
    self._pos = 0
    self._mode = mode

    try:
      self._stat = fs.stats(path)
      if self._stat.isDir:
        raise IOError(errno.EISDIR, "Is a directory: '%s'" % (path,))
    except IOError, ex:
      if ex.errno == errno.ENOENT and mode == 'r':
        raise ex
      self._stat = None

  def seek(self, offset, whence=0):
    """Set the file pointer to the given spot. @see file.seek"""
    if whence == SEEK_SET:
      self._pos = offset
    elif whence == SEEK_CUR:
      self._pos += offset
    elif whence == SEEK_END:
      self.stat()
      self._pos = self._fs.stats(self._path).length + offset
    else:
      raise IOError(errno.EINVAL, "Invalid argument to seek for whence")

  def stat(self):
    self._stat = self._fs.stats(self._path)
    return self._stat

  def tell(self):
    return self._pos

  def read(self, length=DEFAULT_READ_SIZE):
    data = self._fs.read(self._path, self._pos, length)
    self._pos += len(data)
    return data

  def write(self, data):
    """Append the data to the end of the file"""
    self.append(data)

  def append(self, data):
    if 'w' not in self._mode:
      raise IOError(errno.EINVAL, "File not open for writing")

    if self._stat is None:
      # File not there yet.
      self._fs.create(self._path, data=data)
    else:
      self._fs.append(self._path, data=data)

  def close(self):
    pass


def safe_octal(octal_value):
  """
  safe_octal(octal_value) -> octal value in string

  This correctly handles octal values specified as a string or as a numeric.
  """
  try:
    return oct(octal_value)
  except TypeError:
    return str(octal_value)

def _get_service_url(hdfs_config):
  override = hdfs_config.WEBHDFS_URL.get()
  if override:
    return override

  host = hdfs_config.NN_HOST.get()
  port = hdfs_config.NN_HTTP_PORT.get()
  return "http://%s:%s/webhdfs/v1" % (host, port)
