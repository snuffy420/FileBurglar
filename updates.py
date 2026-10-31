"""Small HTTPS release-metadata check; never downloads or executes software."""
import json
import re
import urllib.error
import urllib.request
from urllib.parse import urlparse

REPOSITORY='snuffy420/FileBurglar'
API=f'https://api.github.com/repos/{REPOSITORY}/releases/latest'
MAX_RESPONSE=512*1024


def version(value):
    match=re.fullmatch(r'v?(\d+)\.(\d+)\.(\d+)',str(value))
    return tuple(map(int,match.groups())) if match else None


def validate_release(data,current):
    if not isinstance(data,dict) or data.get('draft') or data.get('prerelease'):
        return None
    candidate=version(data.get('tag_name'));installed=version(current)
    if not candidate or not installed or candidate<=installed:return None
    url=data.get('html_url','')
    parsed=urlparse(url)
    if parsed.scheme!='https' or parsed.netloc!='github.com' or not parsed.path.startswith('/'+REPOSITORY+'/releases/tag/'):
        raise ValueError('Unexpected release link')
    return {'version':'.'.join(map(str,candidate)),'url':url}


def check(current,etag=None):
    headers={'Accept':'application/vnd.github+json','User-Agent':'FileBurglar/'+current,
             'X-GitHub-Api-Version':'2022-11-28'}
    if etag:headers['If-None-Match']=etag
    request=urllib.request.Request(API,headers=headers)
    try:
        with urllib.request.urlopen(request,timeout=8) as response:
            raw=response.read(MAX_RESPONSE+1)
            if len(raw)>MAX_RESPONSE:raise ValueError('Release response too large')
            return validate_release(json.loads(raw),current),response.headers.get('ETag')
    except urllib.error.HTTPError as error:
        if error.code==304:return None,etag
        raise
