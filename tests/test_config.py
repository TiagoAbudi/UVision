from camera_ai.config import Settings


def test_credentials_are_url_encoded() -> None:
    settings = Settings(
        camera_url="rtsp://192.168.18.134:554/cam/realmonitor?channel=1&subtype=0",
        camera_user="admin",
        camera_password="a:b@c",
    )

    url = settings.authenticated_camera_url()

    assert "admin:a%3Ab%40c@" in url
    assert "channel=1&subtype=0" in url


def test_rejects_credentials_inside_camera_url() -> None:
    user = "example-user"
    password = "example-password"
    unsafe_url = "rtsp://" + user + ":" + password + "@" + "192.168.18.134/live"
    settings = Settings(
        camera_url=unsafe_url,
        camera_user=user,
        camera_password=password,
    )

    try:
        settings.validate()
    except ValueError as exc:
        assert "separados" in str(exc)
    else:
        raise AssertionError("A URL com credenciais deveria ser recusada")

