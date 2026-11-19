from __future__ import annotations

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QByteArray, QObject, QUrl
from camera_ai.hybrid_ui import LocalOnly, LocalResources


@pytest.mark.parametrize('url',['https://example.org','http://127.0.0.1:8000','file:///C:/private.txt','qrc:///other.js','uvision://other/index.html'])
def test_webview_rejects_network_files_and_other_origins(url):
    blocked=[]
    LocalOnly().interceptRequest(SimpleNamespace(requestUrl=lambda:QUrl(url),block=blocked.append))
    assert blocked==[True]


class Request(QObject):
    def __init__(self,url,method=b'GET'):
        super().__init__();self.url=QUrl(url);self.method=method;self.error=None;self.data=None
    def requestMethod(self):return QByteArray(self.method)
    def requestUrl(self):return self.url
    def fail(self,error):self.error=error
    def reply(self,mime,buffer):self.data=(bytes(mime),bytes(buffer.readAll()))


def test_local_resources_serve_built_files_and_block_traversal(tmp_path):
    assets=tmp_path/'assets';assets.mkdir();(assets/'index.html').write_text('local-ui',encoding='utf-8')
    (tmp_path/'private.txt').write_text('private',encoding='utf-8')
    handler=LocalResources(SimpleNamespace(image=lambda key:b'image' if key=='4' else None));handler.assets=assets
    good=Request('uvision://app/index.html');handler.requestStarted(good)
    assert good.data==(b'text/html',b'local-ui')
    bad=Request('uvision://app/../private.txt');handler.requestStarted(bad)
    assert bad.error is not None and bad.data is None
    post=Request('uvision://app/index.html',b'POST');handler.requestStarted(post)
    assert post.error is not None
    image=Request('uvision://app/image/4');handler.requestStarted(image)
    assert image.data==(b'image/jpeg',b'image')
