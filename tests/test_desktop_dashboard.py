from __future__ import annotations

from camera_ai.desktop_dashboard import fit_size, mosaic_tile_at


def test_fit_size_preserves_aspect_ratio() -> None:
    assert fit_size(1920, 1080, 1200, 800) == (1200, 675)
    assert fit_size(1920, 1080, 800, 800) == (800, 450)


def test_mosaic_tile_at_accounts_for_letterboxing() -> None:
    bounds = (100, 50, 900, 600)
    assert mosaic_tile_at(101, 51, bounds, 9) == 0
    assert mosaic_tile_at(550, 350, bounds, 9) == 4
    assert mosaic_tile_at(999, 649, bounds, 9) == 8
    assert mosaic_tile_at(50, 350, bounds, 9) is None
