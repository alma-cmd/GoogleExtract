import os
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
import piexif

MEDIA_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.heif',
                    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}


def to_dms(decimal):
    """Конвертирует десятичные координаты GPS в формат EXIF."""
    decimal = abs(decimal)
    d = int(decimal)
    m = int((decimal - d) * 60)
    s = (decimal - d - m / 60) * 3600
    return ((d, 1), (m, 1), (int(s * 100), 100))


def find_json_for_media(media_path):
    """Ищет соответствующий JSON файл."""
    json_path = media_path.with_suffix(media_path.suffix + '.json')
    if json_path.exists():
        return json_path
    parent = media_path.parent
    stem = media_path.stem
    for f in parent.glob(f"{stem}*.json"):
        return f
    return None


def write_exif(file_path, date_taken, geo_data, description):
    """Записывает метаданные в EXIF теги JPEG."""
    try:
        exif_dict = piexif.load(str(file_path))
    except Exception:
        exif_dict = {"0th": {}, "Exif": {}, "GPS": {}, "1st": {}}

    date_str = date_taken.strftime('%Y:%m:%d %H:%M:%S').encode()
    exif_dict["0th"][piexif.ImageIFD.DateTime] = date_str
    exif_dict["Exif"][piexif.ExifIFD.DateTimeOriginal] = date_str
    exif_dict["Exif"][piexif.ExifIFD.DateTimeDigitized] = date_str

    if description:
        exif_dict["0th"][piexif.ImageIFD.ImageDescription] = description.encode('utf-8')

    if geo_data and geo_data.get('latitude') != 0.0 and geo_data.get('longitude') != 0.0:
        lat = geo_data['latitude']
        lon = geo_data['longitude']
        alt = geo_data.get('altitude', 0)

        exif_dict["GPS"][piexif.GPSIFD.GPSLatitudeRef] = b'N' if lat >= 0 else b'S'
        exif_dict["GPS"][piexif.GPSIFD.GPSLatitude] = to_dms(lat)
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitudeRef] = b'E' if lon >= 0 else b'W'
        exif_dict["GPS"][piexif.GPSIFD.GPSLongitude] = to_dms(lon)

        if alt:
            exif_dict["GPS"][piexif.GPSIFD.GPSAltitudeRef] = 0 if alt >= 0 else 1
            exif_dict["GPS"][piexif.GPSIFD.GPSAltitude] = (int(abs(alt) * 100), 100)

    exif_bytes = piexif.dump(exif_dict)
    piexif.insert(exif_bytes, str(file_path))


def process_media_file(media_path, dest_root):
    """Обрабатывает один медиафайл."""
    json_path = find_json_for_media(media_path)
    date_taken = None
    geo_data = None
    description = None

    if json_path and json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            timestamp = data.get('photoTakenTime', {}).get('timestamp')
            if not timestamp:
                timestamp = data.get('creationTime', {}).get('timestamp')
            if timestamp:
                date_taken = datetime.fromtimestamp(int(timestamp))
            geo_data = data.get('geoData', data.get('geoDataExif'))
            description = data.get('description')
        except Exception as e:
            return False, f"Ошибка чтения JSON {json_path}: {e}"

    if not date_taken:
        date_taken = datetime.fromtimestamp(os.path.getmtime(media_path))

    year = date_taken.strftime('%Y')
    month = date_taken.strftime('%m')
    dest_dir = Path(dest_root) / year / month
    dest_dir.mkdir(parents=True, exist_ok=True)

    dest_path = dest_dir / media_path.name
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{media_path.stem}_{counter}{media_path.suffix}"
        counter += 1

    shutil.move(str(media_path), str(dest_path))
    os.utime(dest_path, (date_taken.timestamp(), date_taken.timestamp()))

    if media_path.suffix.lower() in ['.jpg', '.jpeg']:
        try:
            write_exif(dest_path, date_taken, geo_data, description)
        except Exception as e:
            return True, f"EXIF не записан в {dest_path.name}: {e}"

    if json_path and json_path.exists():
        try:
            os.remove(json_path)
        except Exception:
            pass

    return True, f"OK: {media_path.name} -> {year}/{month}"


def collect_media_files(work_dir):
    """Собирает список всех медиафайлов для обработки."""
    files = []
    for root, _, filenames in os.walk(work_dir):
        for file in filenames:
            file_path = Path(root) / file
            if file_path.suffix.lower() in MEDIA_EXTENSIONS:
                files.append(file_path)
    return files


def run_organizer(source_path, dest_path, log_callback=None, progress_callback=None):
    """
    Главная функция запуска.
    log_callback(text) — вызывается для вывода лога.
    progress_callback(current, total) — вызывается для обновления прогресса.
    """
    def log(msg):
        if log_callback:
            log_callback(msg)

    source = Path(source_path)
    dest = Path(dest_path)

    if not source.exists():
        log("❌ Указанный путь не существует!")
        return False

    temp_extract_dir = None

    # Если указан ZIP — распаковываем
    if source.is_file() and source.suffix.lower() == '.zip':
        log("📦 Распаковка архива (это может занять время)...")
        temp_extract_dir = Path("temp_takeout_extract")
        temp_extract_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(source, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_dir)
            work_dir = temp_extract_dir
        except Exception as e:
            log(f"❌ Ошибка распаковки: {e}")
            return False
    else:
        work_dir = source

    log("🔍 Поиск медиафайлов...")
    files = collect_media_files(work_dir)
    total = len(files)
    log(f"Найдено файлов: {total}")

    if total == 0:
        log("⚠️ Медиафайлы не найдены.")
        if temp_extract_dir:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return True

    processed = 0
    errors = 0

    for file_path in files:
        try:
            ok, msg = process_media_file(file_path, dest)
            if not ok:
                errors += 1
                log(f"⚠️ {msg}")
            processed += 1
            if progress_callback:
                progress_callback(processed, total)
        except Exception as e:
            errors += 1
            log(f"❌ Ошибка {file_path.name}: {e}")

    if temp_extract_dir and temp_extract_dir.exists():
        log("🧹 Удаление временных файлов...")
        shutil.rmtree(temp_extract_dir, ignore_errors=True)

    log(f"\n✅ Готово! Обработано: {processed}, ошибок: {errors}")
    log(f"📁 Результат: {dest.absolute()}")
    return True