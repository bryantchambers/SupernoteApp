import filecmp
import os
import shutil
import subprocess
from datetime import timedelta
from pathlib import Path
from urllib import error, request as urlrequest

from django.conf import settings
from django.utils import timezone
from django.utils.text import slugify

from .models import PeriodicalIssue, PeriodicalRecipe


RAW_RECIPE_BASE = "https://raw.githubusercontent.com/kovidgoyal/calibre/master/recipes"

CURATED_RECIPES = [
    {"slug": "bbc-news", "title": "BBC News", "filename": "bbc.recipe", "renew_interval_days": 1, "retention_days": 7},
    {"slug": "bbc-uk", "title": "BBC UK", "filename": "bbc_uk.recipe", "renew_interval_days": 1, "retention_days": 7},
    {"slug": "nature", "title": "Nature", "filename": "nature.recipe", "renew_interval_days": 7, "retention_days": 21},
    {"slug": "the-atlantic", "title": "The Atlantic", "filename": "atlantic.recipe", "renew_interval_days": 7, "retention_days": 21},
    {"slug": "the-new-yorker", "title": "The New Yorker", "filename": "new_yorker.recipe", "renew_interval_days": 7, "retention_days": 30},
    {"slug": "the-economist", "title": "The Economist", "filename": "economist.recipe", "renew_interval_days": 7, "retention_days": 30},
    {"slug": "boston-globe", "title": "The Boston Globe", "filename": "boston_globe.recipe", "renew_interval_days": 1, "retention_days": 14},
    {"slug": "la-times", "title": "LA Times", "filename": "la_times.recipe", "renew_interval_days": 1, "retention_days": 14},
]


class PeriodicalError(RuntimeError):
    pass


NO_ARTICLES_PATTERNS = (
    "NoArticles",
    "Could not find any articles",
    "recipe needs to be updated",
    "server is having trouble",
)


def seed_curated_recipes():
    recipes = []
    for entry in CURATED_RECIPES:
        url = f"{RAW_RECIPE_BASE}/{entry['filename']}"
        recipe, created = PeriodicalRecipe.objects.get_or_create(
            slug=entry["slug"],
            defaults={
                "title": entry["title"],
                "recipe_url": url,
                "renew_interval_days": entry["renew_interval_days"],
                "retention_days": entry["retention_days"],
                "enabled": True,
            },
        )
        updates = []
        if not created:
            if recipe.title != entry["title"]:
                recipe.title = entry["title"]
                updates.append("title")
            if recipe.recipe_url != url:
                recipe.recipe_url = url
                updates.append("recipe_url")
            if not recipe.enabled and not recipe.last_fetched_at and recipe.last_status == "idle":
                recipe.enabled = True
                updates.append("enabled")
            if updates:
                updates.append("updated_at")
                recipe.save(update_fields=updates)
        recipes.append(recipe)
    return recipes


def _recipe_cache_path(recipe):
    if recipe.recipe_path:
        return Path(recipe.recipe_path)
    return Path(settings.PERIODICAL_RECIPE_DIR) / f"{recipe.slug}.recipe"


def _relative_to_archive(path):
    try:
        return Path(path).relative_to(settings.PERIODICAL_ARCHIVE_DIR).as_posix()
    except ValueError:
        return str(path)


def _archive_path(value):
    path = Path(value)
    if path.is_absolute():
        return path
    return Path(settings.PERIODICAL_ARCHIVE_DIR) / path


def _download_recipe(recipe, force=False):
    path = _recipe_cache_path(recipe)
    if path.exists() and not force:
        return path
    if not recipe.recipe_url:
        raise PeriodicalError("Recipe has no source URL.")

    path.parent.mkdir(parents=True, exist_ok=True)
    req = urlrequest.Request(recipe.recipe_url, headers={"User-Agent": "SupernoteApp/periodicals"})
    try:
        with urlrequest.urlopen(req, timeout=30) as resp:
            payload = resp.read()
    except error.URLError as exc:
        raise PeriodicalError(f"Recipe download failed: {exc}") from exc

    path.write_bytes(payload)
    recipe.recipe_path = str(path)
    recipe.last_checked_at = timezone.now()
    recipe.last_status = "ready"
    recipe.last_message = "Recipe cached."
    recipe.save(update_fields=["recipe_path", "last_checked_at", "last_status", "last_message", "updated_at"])
    return path


def update_recipe(recipe):
    return _download_recipe(recipe, force=True)


def is_recipe_due(recipe, now=None):
    if not recipe.enabled:
        return False
    if recipe.last_fetched_at is None:
        return True
    now = now or timezone.now()
    return recipe.last_fetched_at + timedelta(days=recipe.renew_interval_days) <= now


def due_recipes(now=None):
    now = now or timezone.now()
    return [recipe for recipe in PeriodicalRecipe.objects.filter(enabled=True) if is_recipe_due(recipe, now=now)]


def _issue_output_path(recipe, now=None):
    now = now or timezone.now()
    output_format = getattr(settings, "PERIODICAL_OUTPUT_FORMAT", "epub").lower()
    date_slug = now.strftime("%Y-%m-%d")
    title_slug = slugify(recipe.title) or recipe.slug
    filename = f"{date_slug}-{title_slug}.{output_format}"
    return Path(settings.PERIODICAL_ARCHIVE_DIR) / "issues" / recipe.slug / filename


def _issue_device_filename(issue):
    issue_date = issue.issue_date or (issue.fetched_at.date() if issue.fetched_at else timezone.localdate())
    title_slug = slugify(issue.recipe.title) or issue.recipe.slug
    output_format = (issue.output_format or getattr(settings, "PERIODICAL_OUTPUT_FORMAT", "epub")).lower()
    return f"{issue_date.isoformat()}-{title_slug}.{output_format}"


def _news_device_root():
    return Path(settings.PERIODICAL_DEVICE_DIR)


def _news_device_target_path(issue):
    return _news_device_root() / _issue_device_filename(issue)


def _dedupe_device_target(root, filename):
    candidate = root / filename
    if not candidate.exists():
        return candidate

    suffix = candidate.suffix
    stem = candidate.stem
    counter = 2
    while True:
        deduped = root / f"{stem}-{counter}{suffix}"
        if not deduped.exists():
            return deduped
        counter += 1


def flatten_news_device_folder(root=None, dry_run=False):
    root = Path(root) if root is not None else _news_device_root()
    root.mkdir(parents=True, exist_ok=True)
    moves = {}

    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root)
        if len(rel.parts) <= 1:
            continue

        target = root / rel.name
        if target.exists():
            if filecmp.cmp(path, target, shallow=False):
                moves[str(path)] = str(target)
                if not dry_run:
                    path.unlink()
                continue
            target = _dedupe_device_target(root, target.name)

        moves[str(path)] = str(target)
        if dry_run:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(path), str(target))

    if not dry_run:
        for directory in sorted((p for p in root.rglob('*') if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
            try:
                directory.rmdir()
            except OSError:
                pass

    return {'root': str(root), 'moves': moves}


def _calibre_command(recipe_path, output_path):
    return [
        str(getattr(settings, "CALIBRE_EBOOK_CONVERT", "ebook-convert")),
        str(recipe_path),
        str(output_path),
        "--output-profile",
        "generic_eink",
        "--base-font-size",
        "14",
        "--margin-left",
        "8",
        "--margin-right",
        "8",
        "--margin-top",
        "8",
        "--margin-bottom",
        "8",
    ]


def _credential_args(recipe):
    args = []
    username = os.environ.get(recipe.username_env, "") if recipe.username_env else ""
    password = os.environ.get(recipe.password_env, "") if recipe.password_env else ""
    if username:
        args.extend(["--username", username])
    if password:
        args.extend(["--password", password])
    return args


def _run_calibre_fetch(command, debug=False, recipe=None):
    debug_path = ""
    if debug and recipe is not None:
        debug_dir = Path(settings.PERIODICAL_ARCHIVE_DIR) / "debug" / recipe.slug / timezone.now().strftime("%Y%m%d-%H%M%S")
        debug_dir.mkdir(parents=True, exist_ok=True)
        command.extend(["--test", "-vv", "--debug-pipeline", str(debug_dir)])
        debug_path = _relative_to_archive(debug_dir)
    result = subprocess.run(command, capture_output=True, text=True, timeout=900)
    return result, debug_path


def _combined_log(result):
    return "\n".join(part for part in [result.stdout.strip(), result.stderr.strip()] if part)


def _is_no_articles_failure(log_text):
    lowered = (log_text or "").lower()
    return any(pattern.lower() in lowered for pattern in NO_ARTICLES_PATTERNS)


def fetch_periodical(recipe, debug=False, deliver_to_device=True):
    recipe.last_status = "running"
    recipe.last_message = "Fetching periodical."
    recipe.save(update_fields=["last_status", "last_message", "updated_at"])

    try:
        recipe_path = _download_recipe(recipe)
        output_path = _issue_output_path(recipe)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        command = _calibre_command(recipe_path, output_path)
        command.extend(_credential_args(recipe))
        result, debug_path = _run_calibre_fetch(command, debug=debug, recipe=recipe)
    except FileNotFoundError as exc:
        message = f"Calibre ebook-convert was not found: {getattr(settings, 'CALIBRE_EBOOK_CONVERT', 'ebook-convert')}"
        recipe.last_status = "missing_calibre"
        recipe.last_message = message
        recipe.save(update_fields=["last_status", "last_message", "updated_at"])
        raise PeriodicalError(message) from exc
    except Exception as exc:
        recipe.last_status = "error"
        recipe.last_message = str(exc)
        recipe.save(update_fields=["last_status", "last_message", "updated_at"])
        raise

    combined_log = _combined_log(result)
    if (result.returncode != 0 or not output_path.exists()) and _is_no_articles_failure(combined_log) and recipe.recipe_url:
        recipe.last_message = "Recipe index was empty. Refreshing the cached recipe and retrying once."
        recipe.save(update_fields=["last_message", "updated_at"])
        recipe_path = _download_recipe(recipe, force=True)
        if output_path.exists():
            output_path.unlink()
        retry_command = _calibre_command(recipe_path, output_path)
        retry_command.extend(_credential_args(recipe))
        retry_result, retry_debug_path = _run_calibre_fetch(retry_command, debug=debug, recipe=recipe)
        if retry_debug_path:
            debug_path = retry_debug_path
        combined_log = "\n\n".join(part for part in [combined_log, _combined_log(retry_result)] if part)
        result = retry_result

    if result.returncode != 0 or not output_path.exists():
        message = combined_log or f"ebook-convert exited with status {result.returncode}."
        recipe.last_status = "fetch_failed"
        recipe.last_message = message
        recipe.save(update_fields=["last_status", "last_message", "updated_at"])
        raise PeriodicalError(message)

    now = timezone.now()
    issue = PeriodicalIssue.objects.create(
        recipe=recipe,
        title=f"{recipe.title} - {now.date().isoformat()}",
        issue_date=now.date(),
        output_format=getattr(settings, "PERIODICAL_OUTPUT_FORMAT", "epub").lower(),
        archive_path=_relative_to_archive(output_path),
        debug_path=debug_path,
        status="ready",
        status_message=combined_log[:4000],
        expires_at=now + timedelta(days=recipe.retention_days),
    )
    if deliver_to_device:
        send_issue_to_device(issue)

    recipe.last_fetched_at = now
    recipe.last_status = "success"
    recipe.last_message = f"Fetched {issue.title}."
    recipe.save(update_fields=["last_fetched_at", "last_status", "last_message", "updated_at"])
    return issue


def _relative_device_path(device_path):
    try:
        return Path(device_path).relative_to(settings.SUPERNOTE_SOURCE).as_posix()
    except ValueError:
        return str(device_path)


def send_issue_to_device(issue):
    source_path = _archive_path(issue.archive_path)
    if not source_path.exists():
        issue.status = "missing"
        issue.status_message = f"Archived issue file not found: {source_path}"
        issue.save(update_fields=["status", "status_message", "updated_at"])
        raise PeriodicalError(issue.status_message)

    target_path = _news_device_target_path(issue)
    target_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source_path, target_path)

    issue.device_path = _relative_device_path(target_path)
    issue.is_on_device = True
    issue.status = "on_device"
    issue.status_message = "Copied to Supernote News folder."
    issue.save(update_fields=["device_path", "is_on_device", "status", "status_message", "updated_at"])
    return {"device_path": str(target_path)}


def remove_issue_from_device(issue):
    if issue.device_path:
        device_path = Path(issue.device_path)
        if not device_path.is_absolute():
            device_path = Path(settings.SUPERNOTE_SOURCE) / device_path
    else:
        device_path = None

    if device_path and device_path.exists():
        device_path.unlink()

    issue.device_path = ""
    issue.is_on_device = False
    issue.status = "ready"
    issue.status_message = "Removed from Supernote News folder."
    issue.save(update_fields=["device_path", "is_on_device", "status", "status_message", "updated_at"])
    return {"removed": True}


def delete_issue(issue):
    if issue.is_on_device:
        remove_issue_from_device(issue)
    archive_path = _archive_path(issue.archive_path)
    if archive_path.exists():
        archive_path.unlink()
    issue.delete()
    return {"deleted": True}


def prune_expired_issues(now=None):
    now = now or timezone.now()
    deleted = 0
    for issue in PeriodicalIssue.objects.filter(is_pinned=False, expires_at__lt=now):
        delete_issue(issue)
        deleted += 1
    return deleted


def fetch_due_periodicals():
    fetched = []
    errors = []
    for recipe in due_recipes():
        try:
            fetched.append(fetch_periodical(recipe))
        except Exception as exc:
            errors.append((recipe, str(exc)))
    pruned = prune_expired_issues()
    return {"fetched": fetched, "errors": errors, "pruned": pruned}
