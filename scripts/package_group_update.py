"""Build a compact, self-contained handoff ZIP for the current demo."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import zipfile


ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT.parent / 'big_data_update_2'
ZIP = ROOT.parent / 'big_data_update_2.zip'
RUN = 'strength-20261022-v1'
SOURCE_DIRS = ('config', 'orchestration', 'phase2', 'phase3', 'pipeline', 'scripts', 'service', 'tests', 'docs')
SOURCE_FILES = ('.dockerignore', '.env.example', '.gitignore', 'compose.yaml', 'Dockerfile',
                'requirements.txt', 'requirements-ml.txt')
EVIDENCE = ('multilabel_strength_validation.json', 'multilabel_strength_heldout.json',
            'session_learned_ranker_validation.json')
README = '''# big_data_update_2 — RetailRocket Phase 1–7

Gói gọn để chia sẻ nhóm, cập nhật ngày 22/10/2026. Có CSV gốc 2.756.101 dòng,
mã nguồn pipeline/API/dashboard, cấu hình Docker, kiểm thử, và một snapshot phục vụ
đã huấn luyện trên 2.755.641 sự kiện sạch. Không kèm Docker image hay Docker volume.

## Chạy nhanh demo theo phiên (Windows + Docker Desktop)

Mở PowerShell tại thư mục này, sau khi Docker Desktop dùng Linux engine:

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_session_demo.ps1
```

Mở http://localhost:8000/#session-section. Lần đầu Docker build cần Internet;
script dùng snapshot có sẵn, không huấn luyện lại. Các lần sau có thể thêm `-SkipBuild`.

Snapshot đang chọn: `strength-20261022-v1`. Đánh giá 5.000 truy vấn lịch sử,
mọi sản phẩm mới trong phần còn lại của phiên: Precision@10 = 4,148%,
Recall@10 = 28,28%, NDCG@10 = 19,70%. Tập test đã từng được xem trong quá trình
phát triển, nên cần dữ liệu tương lai để xác nhận mức tăng ổn định.

Xem `docs/SESSION_STRENGTH_UPDATE.md` để biết phương pháp và giới hạn.
Các JSON kiểm chứng chính nằm trong `outputs/evidence/`.

## Chạy lại toàn bộ pipeline

```powershell
powershell -ExecutionPolicy Bypass -File scripts/run_demo.ps1
```

Quy trình đầy đủ cần Docker, Internet và nhiều RAM hơn demo nhanh.
Xem `docs/SESSION_GUIDE.md` và mã nguồn để biết từng bước. CSV nguồn nằm ở
`data/raw/events.csv`. Kết quả chạy mới sẽ được ghi vào `outputs/`.

Gói đã bỏ bốn snapshot cũ, example_results, log/smoke tạm và file cache.
`scripts/experiment_session_learned_ranker.py` là thử nghiệm chưa được triển khai
vì không vượt bản hiện tại trên validation.
'''


def keep(_directory, names):
    return {name for name in names if name == '__pycache__' or name.endswith(('.pyc', '.log'))}


def copy_file(relative):
    source, target = ROOT / relative, DEST / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)


def run():
    if ZIP.exists():
        raise FileExistsError('Target ZIP already exists; inspect it before replacing.')
    DEST.mkdir(exist_ok=True)
    for name in SOURCE_DIRS:
        shutil.copytree(ROOT / name, DEST / name, ignore=keep, dirs_exist_ok=True)
    for name in SOURCE_FILES:
        copy_file(name)
    for name in ('data/raw/events.csv', 'data/raw/category_tree.csv',
                 'data/sample/events_10000.csv', 'data/metadata/manifest.json',
                 'data/metadata/source_metadata.json', 'outputs/latest_run.json'):
        copy_file(name)
    shutil.copytree(ROOT / 'outputs/runs' / RUN, DEST / 'outputs/runs' / RUN, dirs_exist_ok=True)
    for name in EVIDENCE:
        copy_file(f'outputs/evidence/{name}')
    # Dockerfile accepts an empty wheel directory and downloads pinned packages.
    copy_file('vendor_wheels/README.md')
    (DEST / 'README.md').write_text(README, encoding='utf-8')
    required = [DEST / 'outputs/runs' / RUN / 'serving' / name for name in
                ('model_bundle.json', 'seen_items.sqlite', 'session_neighbors.sqlite')]
    if not all(p.is_file() and p.stat().st_size for p in required):
        raise RuntimeError('Serving snapshot is incomplete')
    pointer = json.loads((DEST / 'outputs/latest_run.json').read_text(encoding='utf-8'))
    bundle = json.loads(required[0].read_text(encoding='utf-8'))
    if pointer['run_id'] != RUN or bundle['run_id'] != RUN or pointer['model_version'] != bundle['model_version']:
        raise RuntimeError('Pointer does not match bundle')
    hashes = {str(p.relative_to(DEST)).replace('\\', '/'): hashlib.sha256(p.read_bytes()).hexdigest()
              for p in sorted(DEST.rglob('*')) if p.is_file()}
    (DEST / 'PACKAGE_SHA256.json').write_text(json.dumps(hashes, indent=2) + '\n', encoding='utf-8')
    with zipfile.ZipFile(ZIP, 'w', compression=zipfile.ZIP_DEFLATED, compresslevel=6,
                         allowZip64=True) as archive:
        for file in sorted(DEST.rglob('*')):
            if file.is_file():
                archive.write(file, Path(DEST.name) / file.relative_to(DEST))
    with zipfile.ZipFile(ZIP) as archive:
        bad = archive.testzip()
        if bad:
            raise RuntimeError(f'Corrupt archive entry: {bad}')
    print(json.dumps({'folder': str(DEST), 'zip': str(ZIP),
                      'zip_mb': round(ZIP.stat().st_size / 1048576, 2),
                      'files': len(hashes) + 1}, ensure_ascii=False))


if __name__ == '__main__':
    run()
