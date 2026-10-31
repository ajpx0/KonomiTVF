"""キャプチャの保存先フォルダのスキャン処理に関連する E2E テスト。"""

import asyncio
from collections.abc import Callable
from pathlib import Path

import httpx2 as httpx
import pytest

from app.metadata.CaptureScanTask import CaptureScanTask
from tests.conftest import FIXTURES_DIR


async def WaitUntil(condition: Callable[[], bool], timeout: float = 30.0) -> None:
    """
    条件の関数が True を返すまで、timeout 秒を上限に待つ
    """

    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while condition() is False:
        assert loop.time() < deadline, f'Condition was not met within {timeout} seconds.'
        await asyncio.sleep(0.2)


# =====================================================================================
# 複合 E2E テスト
# 複数の API や保存先のファイル操作が絡むフローのテスト
# =====================================================================================

# -------------------------------------------------------------------------------------
# KonomiTV 自身がキャプチャを削除したときと、外で画像が消されたときの監視のフロー
# (DELETE /api/captures → 保存先の監視 → CaptureScanTask.runBatchScan()) - 正常系
# -------------------------------------------------------------------------------------


async def test_delete_capture__does_not_start_batch_scan(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    capture_folder: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    API で削除したキャプチャの削除の通知では保存先の一括スキャンを始めず、
    KonomiTV の外で消された画像の削除の通知では、消えたファイルを確定するために一括スキャンを始める
    """

    # 監視から始まる一括スキャンの回数を数える
    scan_count = 0
    original_run_batch_scan = CaptureScanTask.runBatchScan
    async def CountingRunBatchScan(self: CaptureScanTask, folders: list[str] | None = None) -> bool:
        nonlocal scan_count
        scan_count += 1
        return await original_run_batch_scan(self, folders)
    monkeypatch.setattr(CaptureScanTask, 'runBatchScan', CountingRunBatchScan)
    await WaitUntil(lambda: CaptureScanTask().scan_lock.locked() is False)

    # アップロードしたキャプチャを API で削除しても、一括スキャンは始まらない
    ## 監視の通知は、変更が落ち着くまで (watchfiles の既定で 1.6 秒) まとめてから届くため、それより長く待つ
    file_name = 'Capture_20260826-044039.jpg'
    data = (FIXTURES_DIR / 'captures' / file_name).read_bytes()
    response = await client.post('/api/captures', files={'image': (file_name, data, 'image/jpeg')})
    assert response.status_code == 201, response.text
    capture = response.json()
    response = await client.request('DELETE', '/api/captures', json={'capture_ids': [capture['id']]}, headers=admin_headers)
    assert response.status_code == 200, response.text
    await asyncio.sleep(5)
    assert scan_count == 0

    # KonomiTV の外で画像を消すと、消えたファイルを確定するために一括スキャンが始まる
    response = await client.post('/api/captures', files={'image': (file_name, data, 'image/jpeg')})
    assert response.status_code == 201, response.text
    capture = response.json()
    response = await client.get(f'/api/captures/{capture["id"]}')
    assert response.status_code == 200, response.text
    Path(response.json()['files'][0]['file_path']).unlink()
    await WaitUntil(lambda: scan_count >= 1)
    await WaitUntil(lambda: CaptureScanTask().scan_lock.locked() is False)
    assert list(capture_folder.iterdir()) == []
