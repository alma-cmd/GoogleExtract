import os
import json
import shutil
import zipfile
from datetime import datetime
from pathlib import Path
import piexif

# Расширения файлов, которые обрабатываем
MEDIA_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.heic', '.heif',
                    '.mp4', '.mov', '.avi', '.mkv', '.webm', '.3gp'}

# Сколько первых файлов показать в логе подробно
DEBUG_LOG_LIMIT = 15

# Имя папки для файлов с подозрительной датой
UNSORTED_FOLDER = "Unsorted"


def to_dms(decimal):
    """Конвертирует десятичные координаты GPS в формат EXIF (град/мин/сек)."""
    decimal = abs(decimal)
    d = int(decimal)
    m = int((decimal - d) * 60)
    s = (decimal - d - m / 60) * 3600
    return ((d, 1), (m, 1), (int(s * 100), 100))


def is_suspicious_date(dt):
    """Дата из текущего месяца или из будущего считается подозрительной."""
    now = datetime.now()
    return (dt.year, dt.month) >= (now.year, now.month)


def find_json_for_media(media_path):
    """Ищет JSON-файл, соответствующий медиафайлу."""
    # 1. Точное совпадение: file.jpg.json
    json_path = media_path.with_suffix(media_path.suffix + '.json')
    if json_path.exists():
        return json_path

    # 2. По началу имени (Google добавляет (1), (2) к дубликатам)
    parent = media_path.parent
    stem = media_path.stem
    for f in parent.glob(f"{stem}*.json"):
        return f

    # 3. В родительской папке (нестандартная структура архива)
    for f in parent.parent.glob(f"{stem}*.json"):
        return f

    return None


def extract_date_from_json(data):
    """
    Извлекает дату из JSON Google Takeout.
    Приоритет: photoTakenTime > creationTime > modificationTime.
    Возвращает (datetime, имя_поля) или (None, None).
    """
    timestamp = data.get('photoTakenTime', {}).get('timestamp')
    if timestamp:
        return datetime.fromtimestamp(int(timestamp)), 'photoTakenTime'

    timestamp = data.get('creationTime', {}).get('timestamp')
    if timestamp:
        return datetime.fromtimestamp(int(timestamp)), 'creationTime'

    timestamp = data.get('modificationTime', {}).get('timestamp')
    if timestamp:
        return datetime.fromtimestamp(int(timestamp)), 'modificationTime'

    return None, None


def write_exif(file_path, date_taken, geo_data, description):
    """Записывает метаданные в EXIF-теги JPEG."""
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


def process_media_file(media_path, dest_root, use_unsorted=True):
    """
    Обрабатывает один медиафайл: перемещает в Год/Месяц или Unsorted,
    ставит системные даты, вшивает EXIF (для JPEG).
    Возвращает (ok, message, stats).
    """
    json_path = find_json_for_media(media_path)
    date_taken = None
    geo_data = None
    description = None
    date_source = None
    json_error = False

    if json_path and json_path.exists():
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            date_taken, date_source = extract_date_from_json(data)
            geo_data = data.get('geoData', data.get('geoDataExif'))
            description = data.get('description')
        except Exception as e:
            json_error = True
            date_source = None

    # Fallback: даты в JSON нет или JSON битый — берём дату модификации файла
    fallback_used = False
    if not date_taken:
        date_taken = datetime.fromtimestamp(os.path.getmtime(media_path))
        date_source = 'file_mtime'
        fallback_used = True

    # Целевая папка: Unsorted или Год/Месяц
    unsorted = use_unsorted and is_suspicious_date(date_taken)
    if unsorted:
        dest_dir = Path(dest_root) / UNSORTED_FOLDER
        folder_label = UNSORTED_FOLDER
    else:
        dest_dir = Path(dest_root) / date_taken.strftime('%Y') / date_taken.strftime('%m')
        folder_label = f"{date_taken.strftime('%Y')}/{date_taken.strftime('%m')}"
    dest_dir.mkdir(parents=True, exist_ok=True)

    # Защита от перезаписи дубликатов
    dest_path = dest_dir / media_path.name
    counter = 1
    while dest_path.exists():
        dest_path = dest_dir / f"{media_path.stem}_{counter}{media_path.suffix}"
        counter += 1

    shutil.move(str(media_path), str(dest_path))
    os.utime(dest_path, (date_taken.timestamp(), date_taken.timestamp()))

    # EXIF только для JPEG
    exif_status = ''
    if media_path.suffix.lower() in ['.jpg', '.jpeg']:
        try:
            write_exif(dest_path, date_taken, geo_data, description)
            exif_status = ' [EXIF OK]'
        except Exception:
            exif_status = ' [EXIF ERR]'

    # Удаляем использованный JSON
    if json_path and json_path.exists():
        try:
            os.remove(json_path)
        except Exception:
            pass

    stats = {'fallback': fallback_used, 'unsorted': unsorted, 'json_error': json_error}
    msg = (f"{media_path.name} -> {folder_label} | "
           f"дата={date_taken.strftime('%Y-%m-%d')} ({date_source}){exif_status}")
    return True, msg, stats


def collect_media_files(work_dir):
    """Собирает список всех медиафайлов в дереве папок."""
    files = []
    for root, _, filenames in os.walk(work_dir):
        for file in filenames:
            file_path = Path(root) / file
            if file_path.suffix.lower() in MEDIA_EXTENSIONS:
                files.append(file_path)
    return files


def run_organizer(source_path, dest_path, delete_temp=True, use_unsorted=True,
                  log_callback=None, progress_callback=None):
    """
    Главная функция обработки.
    source_path  — ZIP-файл, папка с ZIP-архивами или распакованная папка.
    dest_path    — куда раскладывать результат.
    delete_temp  — удалять ли временную папку распаковки.
    use_unsorted — складывать ли подозрительные даты в Unsorted.
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
    work_dir = source

    # Сценарий 1: источник — один ZIP-файл
    if source.is_file() and source.suffix.lower() == '.zip':
        log("📦 Распаковка архива...")
        temp_extract_dir = Path("temp_takeout_extract")
        temp_extract_dir.mkdir(exist_ok=True)
        try:
            with zipfile.ZipFile(source, 'r') as zip_ref:
                zip_ref.extractall(temp_extract_dir)
            work_dir = temp_extract_dir
        except Exception as e:
            log(f"❌ Ошибка распаковки: {e}")
            return False

    # Сценарий 2: источник — папка
    elif source.is_dir():
        zip_files = sorted(source.glob("*.zip"))
        if zip_files:
            log(f"📦 Найдено ZIP-архивов: {len(zip_files)}. Распаковка...")
            temp_extract_dir = Path("temp_takeout_extract")
            temp_extract_dir.mkdir(exist_ok=True)
            for i, zip_file in enumerate(zip_files, 1):
                size_mb = zip_file.stat().st_size // 1024 // 1024
                log(f"   [{i}/{len(zip_files)}] {zip_file.name} ({size_mb} МБ)")
                try:
                    with zipfile.ZipFile(zip_file, 'r') as zip_ref:
                        zip_ref.extractall(temp_extract_dir)
                except Exception as e:
                    log(f"   ⚠️ Ошибка распаковки {zip_file.name}: {e}")
            work_dir = temp_extract_dir
        else:
            log("📁 Работа с распакованной папкой...")
            work_dir = source

    log(f"📂 Путь распаковки: {work_dir.absolute()}")

    log("🔍 Поиск медиафайлов...")
    files = collect_media_files(work_dir)
    total = len(files)
    log(f"Найдено файлов: {total}")

    if total == 0:
        log("⚠️ Медиафайлы не найдены.")
        if temp_extract_dir and delete_temp:
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        return True

    processed = 0
    errors = 0
    fallback_count = 0
    unsorted_count = 0
    json_error_count = 0

    log(f"\n{'=' * 60}")
    log(f"📋 Первые {min(DEBUG_LOG_LIMIT, total)} файлов (подробно):")
    log(f"{'=' * 60}")

    for i, file_path in enumerate(files):
        try:
            ok, msg, stats = process_media_file(file_path, dest, use_unsorted=use_unsorted)
            if not ok:
                errors += 1
                log(f"⚠️ {msg}")
            else:
                processed += 1
                if i < DEBUG_LOG_LIMIT:
                    log(f"  [{i + 1}] {msg}")
                if stats.get('json_error'):
                    json_error_count += 1
                    log(f"  ⚠️ Битый JSON у файла {file_path.name} — дата взята из файла")
                if stats.get('fallback'):
                    fallback_count += 1
                if stats.get('unsorted'):
                    unsorted_count += 1
            if progress_callback:
                progress_callback(processed + errors, total)
        except Exception as e:
            errors += 1
            log(f"❌ Ошибка {file_path.name}: {e}")

    log(f"\n{'=' * 60}")
    log("📊 Итоговая статистика:")
    log(f"   Обработано файлов: {processed}")
    log(f"   Ошибок: {errors}")
    if fallback_count:
        log(f"   Дат взято из файла (нет даты в JSON): {fallback_count}")
    if json_error_count:
        log(f"   Битых JSON: {json_error_count}")
    if unsorted_count:
        log(f"   Помещено в '{UNSORTED_FOLDER}': {unsorted_count}")

    if temp_extract_dir and temp_extract_dir.exists():
        if delete_temp:
            log("🧹 Удаление временных файлов...")
            shutil.rmtree(temp_extract_dir, ignore_errors=True)
        else:
            log(f"💾 Временные файлы сохранены: {temp_extract_dir.absolute()}")

    log(f"\n✅ Готово! Результат: {dest.absolute()}")
    return True