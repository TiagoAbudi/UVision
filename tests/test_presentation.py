from __future__ import annotations

import json
import time
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from camera_ai.presentation import PresentationBridge, clip_path
from camera_ai.presentation_actions import save_calibration, validated_regions


@pytest.fixture
def app(monkeypatch):
    monkeypatch.setenv("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def bridge(app,tmp_path):
    instance=PresentationBridge(tmp_path,start_backend=False)
    instance._status_timer.stop();instance._frame_timer.stop()
    yield instance
    instance._media.shutdown(wait=True,cancel_futures=True)
    instance._ptz.shutdown(wait=True,cancel_futures=True)
    instance._commands.shutdown(wait=True,cancel_futures=True)
    if instance._player:instance._player.release()


def test_unknown_actions_and_invalid_payload_are_rejected(bridge):
    replies=[]
    bridge.response.connect(lambda request,result:replies.append(json.loads(result)))
    for action,payload in (("executePython","{}"),("openFolder","[]"),("visible",'{"channels":[{"channel":999,"width":200}]}')):
        bridge.request("id",action,payload)
    assert len(replies)==3
    assert all('error' in result for result in replies)
    assert bridge._visible==[]


def test_video_subscriptions_do_not_start_capture(bridge):
    bridge.request('id','visible',json.dumps({'channels':[{'channel':4,'width':640}]}))
    assert bridge._visible==[{'channel':4,'width':640}]
    assert bridge.backend is None
    bridge.request('id','visible','{"channels":[]}')
    assert bridge._visible==[]


@pytest.mark.parametrize('name',['../secret.mp4','..\\secret.mp4','C:\\secret.mp4','clip.txt'])
def test_clip_paths_cannot_escape_the_recording_folder(tmp_path,name):
    with pytest.raises(ValueError):clip_path(tmp_path,name)


@pytest.mark.parametrize('regions',[[[[0,0],[1,0]]],[[[0,0],[1,0],[float('nan'),1]]],[[[0,0],[1,0],[2,1]]],[[['secret',0],[1,0],[1,1]]]])
def test_invalid_calibration_coordinates_are_rejected(regions):
    with pytest.raises(ValueError):validated_regions(regions)


def test_layout_round_trip_and_invalid_order(bridge):
    layout={'mode':'2x2','order':[9,2,3,4,5,6,7,8,1],'collapsed':True}
    bridge.execute('layout',layout)
    assert json.loads((bridge.root/'runtime/ui-layout.json').read_text())==layout
    with pytest.raises(ValueError):bridge.execute('layout',{**layout,'order':[1]*9})


def test_player_review_seek_and_protection_use_existing_store(bridge):
    folder=bridge.root/'recordings/falls';folder.mkdir(parents=True)
    path=folder/'queda-ch04_2026-11-19_12-00-00.mp4'
    writer=cv2.VideoWriter(str(path),cv2.VideoWriter_fourcc(*'mp4v'),4,(64,64))
    assert writer.isOpened()
    for _ in range(16):writer.write(np.zeros((64,64,3),np.uint8))
    writer.release()
    catalog=bridge.execute('recordings',{})
    assert len(catalog)==1 and catalog[0]['duration']==4
    assert bridge.image('thumb/'+path.name)
    player=bridge.execute('playerOpen',{'file':path.name})
    assert player['duration']==4 and bridge.image('player')
    player=bridge.execute('playerSeek',{'seconds':2})
    assert player['position']==pytest.approx(2,abs=.25)
    assert bridge.execute('playerSpeed',{'speed':2})['speed']==2
    assert bridge.execute('playerPlay',{'playing':True})['playing']
    bridge._last_playback=time.monotonic()-.26
    bridge._frames()
    assert bridge.player['position']==pytest.approx(2.5,abs=.05)
    catalog=bridge.execute('review',{'file':path.name,'label':'unsure'})
    assert catalog[0]['review']=='unsure' and catalog[0]['protected']
    assert bridge.store.reviews()[path.name]['label']=='unsure'
    bridge.execute('protect',{'file':path.name,'enabled':False})
    assert not bridge.store.is_protected(path)


def test_calibration_adapter_preserves_exact_coordinates_and_other_channels(bridge,monkeypatch):
    from camera_ai import web_dashboard
    from camera_ai.web_dashboard import DVRSettings
    monkeypatch.setattr(web_dashboard,'PROJECT_DIR',bridge.root)
    applied=[]
    backend=SimpleNamespace(fall_monitors={4:object()},settings=DVRSettings(host='localhost',user='test',password='test',fall_geometry_profile=''),reload_calibration=applied.append)
    floor=[[[.1,.2],[.8,.2],[.8,.9]]]
    save_calibration(backend,bridge.root,{'channel':4,'floor':floor,'rest':[],'lines':[],'rotation':90,'seconds':4.5})
    data=json.loads((bridge.root/'calibration/ch04-zones.json').read_text())
    assert data['floor']==floor and data['rotationDegrees']==90 and data['groundConfirmSeconds']==4.5
    assert data['coordinateSystem']=='raw_image_normalized_xy'
    assert applied==[4]
    assert not (bridge.root/'calibration/ch01-zones.json').exists()


def test_snapshot_never_exports_the_bot_token(bridge):
    from camera_ai.notifications import TelegramSettings
    secret='123456:'+('x'*30)
    backend=SimpleNamespace(feeds=[],detection_worker=SimpleNamespace(status=lambda:{'ready':True},performance=__import__('camera_ai.performance',fromlist=['PerformanceProfile']).PerformanceProfile()),
                            settings=SimpleNamespace(yolo_image_size=640,detection_interval=.16),notifications=SimpleNamespace(settings=TelegramSettings(token=secret),message='Desativado'),recovery=SimpleNamespace(message=''),started_at='2026-11-19T12:00:00Z')
    bridge.backend=backend
    state=bridge.snapshot()
    assert secret not in json.dumps(state)
    assert state['telegram']['configured']
    assert 'token' not in state['telegram']
    assert len(state['profileChoices'])==3


def test_frame_encoder_reuses_the_existing_feed_and_skips_duplicates(bridge):
    calls=[]
    feed=SimpleNamespace(channel=4,display_snapshot=lambda:(np.zeros((100,200,3),np.uint8),7,[],200,100,True))
    bridge.backend=SimpleNamespace(feed=lambda channel:calls.append(channel) or feed,settings=SimpleNamespace(channel_count=8))
    bridge._visible=[{'channel':4,'width':320}]
    bridge._frames()
    first=bridge.image('4')
    bridge._frames()
    assert bridge.image('4') is first
    assert calls==[4,4]
    assert cv2.imdecode(np.frombuffer(first,np.uint8),cv2.IMREAD_COLOR).shape[:2]==(160,320)
