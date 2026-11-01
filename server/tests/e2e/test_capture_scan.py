"""キャプチャの保存先フォルダのスキャン処理に関連する E2E テスト。"""

import asyncio
import shutil
from collections.abc import Callable
from pathlib import Path

import httpx2 as httpx
import pytest

from app.metadata.CaptureScanTask import CaptureScanTask, NormalizeCapturePath
from app.models.Capture import CaptureFile
from tests.conftest import FIXTURES_DIR, TEMP_DIR


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
# 保存先の一括スキャンで、移動した画像の場所を付け替え、消えた画像を Missing にするフロー
# (保存先の画像の移動・削除 → CaptureScanTask.runBatchScan()) - 正常系
# -------------------------------------------------------------------------------------


async def test_batch_scan__relinks_moved_file_and_marks_removed_file_missing(client: httpx.AsyncClient, admin_headers: dict[str, str]) -> None:
    """
    一括スキャンで、保存先の中で別のフォルダへ移した画像は同じ場所の行のまま新しいパスに付け替わり、
    消えた画像の行は Missing になり、同じ場所に戻すと同じ行のまま利用できる状態に戻る
    """

    # 監視している保存先とは別のフォルダに画像を置き、監視の通知を通さずに一括スキャンだけで登録する
    ## 起動時の一括スキャンや、ほかのテストの削除で始まった一括スキャンが終わるのを待ってから始める
    root = TEMP_DIR / 'batch-scan'
    root.mkdir()
    moved_name = 'Capture_20221003-224010.jpg'
    removed_name = 'Capture_20221219-001337.jpg'
    for file_name in (moved_name, removed_name):
        shutil.copyfile(FIXTURES_DIR / 'captures' / file_name, root / file_name)
    scanner = CaptureScanTask()
    await WaitUntil(lambda: scanner.scan_lock.locked() is False)
    assert await scanner.runBatchScan([str(root)]) is True
    moved_file = await CaptureFile.get(file_path=NormalizeCapturePath(root / moved_name))
    removed_file = await CaptureFile.get(file_path=NormalizeCapturePath(root / removed_name))
    assert moved_file.status == 'Available'
    assert removed_file.status == 'Available'

    # 1枚を子フォルダへ移し、もう1枚を消してから一括スキャンする
    (root / 'moved').mkdir()
    (root / moved_name).rename(root / 'moved' / moved_name)
    removed_data = (root / removed_name).read_bytes()
    (root / removed_name).unlink()
    assert await scanner.runBatchScan([str(root)]) is True

    # 移した画像は、同じ行のまま新しいパスに付け替わる
    relinked_file = await CaptureFile.get(id=moved_file.id)
    assert relinked_file.file_path == NormalizeCapturePath(root / 'moved' / moved_name)
    assert relinked_file.status == 'Available'
    assert relinked_file.capture_id == moved_file.capture_id

    # 消えた画像の行は、すぐには削除されずに Missing になる
    missing_file = await CaptureFile.get(id=removed_file.id)
    assert missing_file.status == 'Missing'
    assert missing_file.missing_since is not None

    # 同じ場所に戻すと、同じ行のまま利用できる状態に戻る
    (root / removed_name).write_bytes(removed_data)
    assert await scanner.runBatchScan([str(root)]) is True
    restored_file = await CaptureFile.get(id=removed_file.id)
    assert restored_file.status == 'Available'
    assert restored_file.missing_since is None

    # ほかのテストの一覧に混ざらないよう、登録したキャプチャを削除する
    response = await client.request('DELETE', '/api/captures', json={'capture_ids': [moved_file.capture_id, removed_file.capture_id]}, headers=admin_headers)
    assert response.status_code == 200, response.text


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


# -------------------------------------------------------------------------------------
# Windows で大文字と小文字だけが異なるパスの場所が2つあるキャプチャを削除するフロー
# (大文字と小文字を区別しないパスの照合 → DELETE /api/captures) - 正常系
# -------------------------------------------------------------------------------------


async def test_delete_capture__with_paths_differing_only_in_case(
    client: httpx.AsyncClient,
    admin_headers: dict[str, str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """
    Windows のように大文字と小文字を区別しないパスの照合で、同じキーになる場所が2つあるキャプチャも、
    同じロックを2回待たずに削除できる
    """

    # Windows と同じく、パスを小文字にしたものを同じファイルかどうかの判定に使う
    monkeypatch.setattr('app.metadata.CaptureScanTask.CapturePathKey', lambda path: path.lower())

    # アップロードしたキャプチャに、名前の大文字と小文字だけを変えたパスの場所をもう1つ登録する
    file_name = 'Capture_20260826-044039.jpg'
    response = await client.post('/api/captures', files={'image': (file_name, (FIXTURES_DIR / 'captures' / file_name).read_bytes(), 'image/jpeg')})
    assert response.status_code == 201, response.text
    capture = response.json()
    uploaded_file = await CaptureFile.get(capture_id=capture['id'])
    renamed_path = str(Path(uploaded_file.file_path).with_name(file_name.lower()))
    await CaptureFile.create(
        capture_id = uploaded_file.capture_id,
        file_path = renamed_path,
        file_name = Path(renamed_path).name,
        file_size = uploaded_file.file_size,
        file_created_at = uploaded_file.file_created_at,
        file_modified_at = uploaded_file.file_modified_at,
        status = 'Available',
    )

    # 削除の API が応答し、キャプチャと2つの場所の行がどちらも削除される
    response = await asyncio.wait_for(
        client.request('DELETE', '/api/captures', json={'capture_ids': [capture['id']]}, headers=admin_headers),
        timeout = 10,
    )
    assert response.status_code == 200, response.text
    assert response.json() == {'deleted_capture_ids': [capture['id']], 'failed_capture_ids': []}
    assert await CaptureFile.filter(capture_id=capture['id']).exists() is False
    await WaitUntil(lambda: CaptureScanTask().scan_lock.locked() is False)
