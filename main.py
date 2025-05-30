import sys
import os
import json
import hashlib
import re
import time
import subprocess
import shutil
import logging
import enum
from pathlib import Path
from urllib.parse import urlparse, urljoin
from typing import Optional, Any, Dict, Callable, List, Tuple, Union  # Union追加

import requests
from bs4 import BeautifulSoup
from packaging import version

from PySide6.QtWidgets import (
    QApplication,
    QMainWindow,
    QWidget,
    QLabel,
    QPushButton,
    QCheckBox,
    QVBoxLayout,
    QHBoxLayout,
    QSystemTrayIcon,
    QMenu,
    QGroupBox,
    QGridLayout,
    QTextEdit,
    QRadioButton,
    QSizePolicy,
    QStyle,
    QFileDialog,
    QScrollArea,
    QLineEdit,
    QGraphicsDropShadowEffect,
)
from PySide6.QtCore import Qt, QSize, QObject, Signal, QThread, QPropertyAnimation, QAbstractAnimation, QEasingCurve, QTimer
from PySide6.QtGui import QIcon, QFontMetrics, QColor

# ----------------------------------------------------------------------
# 1. ロギング設定
# ----------------------------------------------------------------------
logger = logging.getLogger("PS2JPModApp")
logger.setLevel(logging.DEBUG)
console_handler = logging.StreamHandler(sys.stdout)

# Nuitkaコンパイル環境かを判定
IS_FROZEN_APP = "__compiled__" in globals()

if IS_FROZEN_APP:  # Nuitkaコンパイル環境 (または他の凍結ツール)
    console_handler.setLevel(logging.WARNING)
else:  # 通常Python環境
    console_handler.setLevel(logging.INFO)

formatter = logging.Formatter("%(asctime)s - %(levelname)s - [%(name)s] - %(funcName)s:%(lineno)d - %(message)s")
console_handler.setFormatter(formatter)
logger.addHandler(console_handler)
file_handler = None  # グローバル変数として定義、後に設定


# ----------------------------------------------------------------------
# 2. Enum定義
# ----------------------------------------------------------------------
class LaunchMode(enum.IntEnum):
    """ゲームの起動モードを定義するEnum。"""

    NORMAL = 0
    STEAM = 1


# ----------------------------------------------------------------------
# 3. 定数定義
# ----------------------------------------------------------------------
class AppConstants:
    """アプリケーション全体で使用する定数を保持するクラス。"""

    WINDOW_TITLE: str = "PS2JPMod"
    EN_DAT_FILE_NAME: str = "en_us_data.dat"
    EN_DIR_FILE_NAME: str = "en_us_data.dir"
    JP_DAT_FILE_NAME: str = "ja_jp_data.dat"
    JP_DIR_FILE_NAME: str = "ja_jp_data.dir"

    # 設定ファイルキー
    CONFIG_KEY_APP_VERSION: str = "app_version"
    CONFIG_KEY_TRANSLATION_VERSION: str = "translation_version"
    CONFIG_KEY_LAUNCH_MODE: str = "launch_mode"  # 値はLaunchMode Enumを使用
    CONFIG_KEY_LOCAL_PATH: str = "local_path"
    CONFIG_KEY_APP_UPDATE_SERVER_URL: str = "app_server_url"
    CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL: str = "translation_server_url"
    CONFIG_KEY_DEVELOPER_MODE: str = "developer_mode"

    # 設定デフォルト値
    DEFAULT_APP_VERSION: str = "1.0.0"  # 手動更新
    DEFAULT_TRANSLATION_VERSION: str = "0.0.0"
    DEFAULT_LAUNCH_MODE: LaunchMode = LaunchMode.STEAM
    DEFAULT_LOCAL_PATH: str = ""
    DEFAULT_APP_UPDATE_SERVER_URL: str = "PS2-Localization-JP/PlanetSide2-nihongo-mod-ui/"
    DEFAULT_TRANSLATION_UPDATE_SERVER_URL: str = "PS2-Localization-JP/PlanetSide2-nihongo-mod-api/"
    DEFAULT_DEVELOPER_MODE: bool = False

    # UI表示テキスト
    BUTTON_TEXT_LAUNCH_GAME: str = "1:ゲーム起動"
    BUTTON_TEXT_APPLY_TRANSLATION: str = "2:日本語化"
    BUTTON_TEXT_UPDATE: str = "更新"
    BUTTON_TEXT_CHECK_FOR_UPDATES: str = "アップデート確認"

    # Steam関連
    STEAM_GAME_URI: str = "steam://rungameid/218230"

    # リソースパス
    RESOURCE_DIR_NAME: str = "resources"  # リソースディレクトリ名
    ICON_FILE_NAME: str = "ps2jpmod.ico"  # アイコンファイル名

    # フォントファイル名 (data/fonts/ 以下に配置される想定)
    FONT_DIR_NAME: str = "fonts"
    FONT_GEO_MD: str = "Geo-Md.ttf"
    FONT_PS2_GEO_MD_ROSA_VERDE: str = "Ps2GeoMdRosaVerde.ttf"

    # アップデート対象ファイル名
    APP_UPDATE_FILENAMES: List[str] = ["PS2JPMod.exe", "default.txt"]
    TRANSLATION_UPDATE_FILENAMES: List[str] = [JP_DAT_FILE_NAME, JP_DIR_FILE_NAME]

    # UI関連の調整値
    STATUS_DISPLAY_LINE_COUNT: float = 3.5  # ステータス表示欄の行数目安


CONST = AppConstants()


# ----------------------------------------------------------------------
# 4. ユーティリティ関数・クラス
# ----------------------------------------------------------------------


def get_icon_path(base_directory: Path) -> Path:
    """
    アプリケーションのアイコンファイルのパスを解決します。

    Args:
        base_directory: アプリケーションのベースディレクトリ。

    Returns:
        アイコンファイルのPathオブジェクト。
    """
    # Nuitka onefileの場合、resourcesはexeと同階層か、--include-data-dirで指定した場所
    # 通常実行の場合、スクリプトからの相対位置
    # BASE_DIR が src なら、resources は ../resources
    # BASE_DIR がプロジェクトルートなら、resources は ./resources (Nuitkaコンパイル時も同様にビルド指定)

    # Nuitkaで --include-data-dir=src/resources=resources とした場合、
    # 実行ファイルと同階層に resources フォルダが作られる。
    candidate_path = base_directory / CONST.RESOURCE_DIR_NAME / CONST.ICON_FILE_NAME
    if candidate_path.exists():
        return candidate_path

    # フォールバック (開発時など、srcディレクトリがBASE_DIRの場合)
    # src/../resources/ps2jpmod.ico -> project_root/resources/ps2jpmod.ico
    candidate_path_dev_structure = base_directory.parent / CONST.RESOURCE_DIR_NAME / CONST.ICON_FILE_NAME
    if candidate_path_dev_structure.exists():
        return candidate_path_dev_structure

    logger.warning(f"アイコンファイルが見つかりませんでした。試行パス: {candidate_path}, {candidate_path_dev_structure}")
    return candidate_path  # 見つからなくてもパスを返す（QIcon側でエラー処理）


# ----------------------------------------------------------------------
# 5. コアロジッククラス
# ----------------------------------------------------------------------
class JsonConfigManager:
    """JSON形式の設定ファイルを管理するクラス。"""

    def __init__(self, data_directory_path: str):
        self._initial_config_flag: bool = False
        self.config_file_path: Path = Path(data_directory_path) / "config.json"
        self.config: Dict[str, Any] = {}
        self._load_config()
        logger.info(f"設定マネージャー初期化完了: {self.config_file_path}")

    def _load_config(self) -> None:
        logger.debug(f"設定ファイル読み込み開始: {self.config_file_path}")
        try:
            self.config = json.loads(self.config_file_path.read_text(encoding="utf-8"))
            # 新規で追加されたキーがある場合に、キーを網羅的にチェックしてデフォルト値を設定する
            for key, value in type(CONST).__dict__.items():
                if key.startswith("CONFIG_KEY_") and value not in self.config:
                    logger.info(f"新規キー: {value}")
                    default_const_key_name = f"DEFAULT_{key.replace("CONFIG_KEY_", "")}"
                    self.config[value] = type(CONST).__dict__.get(default_const_key_name)
                    if self._save_config():  # 保存成功時のみフラグを立てる
                        logger.info("新規キーを作成・保存しました。")
                    else:
                        logger.error("新規キーの保存に失敗しました。")
            logger.info(f"設定ファイル読み込み成功: {self.config_file_path}")
        except FileNotFoundError:
            logger.warning(f"設定ファイルが見つかりません: {self.config_file_path}。デフォルト設定で新規作成します。")
            self._create_default_config()
        except json.JSONDecodeError as e:
            logger.error(f"設定ファイルが破損しています: {self.config_file_path} ({e})。デフォルト設定で新規作成します。", exc_info=False)
            self._create_default_config()
        except Exception as e:
            logger.error(f"設定ファイルの読み込み中に予期せぬエラー: {e}", exc_info=True)
            self._create_default_config()  # 安全策

    def _create_default_config(self) -> None:
        logger.info("デフォルト設定を作成中...")
        self.config = {
            CONST.CONFIG_KEY_APP_VERSION: CONST.DEFAULT_APP_VERSION,
            CONST.CONFIG_KEY_TRANSLATION_VERSION: CONST.DEFAULT_TRANSLATION_VERSION,
            CONST.CONFIG_KEY_LAUNCH_MODE: CONST.DEFAULT_LAUNCH_MODE.value,  # Enumの値を保存
            CONST.CONFIG_KEY_LOCAL_PATH: CONST.DEFAULT_LOCAL_PATH,
            CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL: CONST.DEFAULT_APP_UPDATE_SERVER_URL,
            CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL: CONST.DEFAULT_TRANSLATION_UPDATE_SERVER_URL,
            CONST.CONFIG_KEY_DEVELOPER_MODE: CONST.DEFAULT_DEVELOPER_MODE,
        }
        if self._save_config():  # 保存成功時のみフラグを立てる
            self._initial_config_flag = True
            logger.info("デフォルト設定を作成・保存しました。")
        else:
            logger.error("デフォルト設定の保存に失敗しました。")

    def _save_config(self) -> bool:
        """現在の設定をファイルに保存します。成功すればTrueを返します。"""
        logger.debug(f"設定ファイル保存開始: {self.config_file_path}")
        try:
            self.config_file_path.parent.mkdir(parents=True, exist_ok=True)
            self.config_file_path.write_text(json.dumps(self.config, indent=4, ensure_ascii=False), encoding="utf-8")
            logger.info(f"設定ファイル保存成功: {self.config_file_path}")
            return True
        except (IOError, OSError) as e:  # PermissionErrorなども含む
            logger.error(f"設定ファイルの保存中にIO/OSエラー: {e}", exc_info=True)
            return False
        except Exception as e:
            logger.error(f"設定ファイルの保存中に予期せぬエラー: {e}", exc_info=True)
            return False

    def get_config_value(self, key: str, default: Optional[Any] = None) -> Any:
        """指定されたキーに対応する設定値を取得します。"""
        value = self.config.get(key, default)
        if key == CONST.CONFIG_KEY_LAUNCH_MODE:  # 起動モードの場合はEnum型で返す
            try:
                return LaunchMode(int(value)) if value is not None else default
            except ValueError:
                logger.warning(f"設定ファイルの起動モード値'{value}'が無効。デフォルト({default})を返します。")
                return default
        return value

    def set_config_value(self, key: str, value: Any) -> None:
        """指定されたキーに値を設定します。設定後、自動的にファイルに保存されます。"""
        actual_value = value.value if isinstance(value, LaunchMode) else value  # Enumなら値を取得
        self.config[key] = actual_value
        if not self._save_config():
            logger.error(f"設定値 '{key}' の保存に失敗しました。変更はメモリ上のみに留まります。")
        else:
            logger.debug(f"設定値更新・保存: {key} = {actual_value}")

    def is_initial_config(self) -> bool:
        """設定ファイルが初期作成されたものかどうかを返します。"""
        return self._initial_config_flag


class FileIntegrityChecker:
    """ファイルの整合性 (ハッシュ値) をチェックするクラス。"""

    def calculate_sha256(self, filepath: Union[str, Path]) -> str:
        """ファイルの SHA-256 ハッシュ値を計算します。"""
        file_p = Path(filepath)
        logger.debug(f"SHA256ハッシュ計算開始: {file_p}")
        sha256_hash = hashlib.sha256()
        try:
            with open(file_p, "rb") as f:
                for byte_block in iter(lambda: f.read(4096), b""):  # 4KBずつ読み込み
                    sha256_hash.update(byte_block)
            hex_digest = sha256_hash.hexdigest()
            logger.debug(f"SHA256ハッシュ計算完了: {file_p} -> {hex_digest}")
            return hex_digest
        except FileNotFoundError:
            logger.error(f"ハッシュ計算エラー: ファイルが見つかりません - {file_p}")
            raise
        except PermissionError:
            logger.error(f"ハッシュ計算エラー: 読み取り権限がありません - {file_p}")
            raise
        except IOError as e:  # その他のIOエラー
            logger.error(f"ハッシュ計算エラー: ファイルI/Oエラー - {file_p}, {e}", exc_info=True)
            raise

    def verify_file_hash(self, filepath: Union[str, Path], expected_sha256: str) -> bool:
        """ファイルのSHA-256ハッシュ値を期待値と比較します。"""
        file_p = Path(filepath)
        logger.debug(f"ファイルハッシュ検証開始: {file_p}, 期待値: {expected_sha256}")
        try:
            calculated_sha256 = self.calculate_sha256(file_p)
            is_match = calculated_sha256.lower() == expected_sha256.lower()
            result_msg = "成功" if is_match else f"失敗 (計算値: {calculated_sha256})"
            logger.info(f"ファイルハッシュ検証{result_msg}: {file_p}")
            return is_match
        except (FileNotFoundError, PermissionError, IOError):  # calculate_sha256からの例外
            logger.warning(f"ファイルハッシュ検証不可: ファイルアクセスエラー - {file_p}", exc_info=False)
            return False


class GitHubReleaseScraper:
    """GitHub Releases ページから情報をスクレイピングするクラス。"""

    BASE_URL: str = "https://github.com"

    def __init__(self) -> None:
        logger.debug("GitHubReleaseScraper 初期化")

    def _get_highest_version_tag(self, releases_info: List[Dict[str, Any]], include_prerelease: bool = True) -> Optional[str]:
        """
        リリース情報のリストから、最もバージョンが高いタグ名を特定して返します。
        Args:
            releases_info: get_all_releases_infoから取得したリリース情報のリスト。
            include_prerelease: プレリリース版（例: v1.0.0-beta）を最高バージョン候補に含めるか。
        Returns:
            最もバージョンが高いタグ名、または有効なタグが見つからない場合はNone。
        """
        logger.debug(f"最高バージョンタグの特定開始 (プレリリース含める: {include_prerelease})")
        highest_version: Optional[version.Version] = None
        highest_version_tag_name: Optional[str] = None

        for release in releases_info:
            try:
                parsed_version = version.parse(release["tag_name"])
                if not include_prerelease and parsed_version.is_prerelease:
                    continue  # プレリリースを除外する設定で、プレリリース版であればスキップ

                if highest_version is None or parsed_version > highest_version:
                    highest_version = parsed_version
                    highest_version_tag_name = release["tag_name"]
            except version.InvalidVersion:
                logger.warning(f"無効なバージョン形式のタグをスキップ: {release['tag_name']}")
                continue

        if highest_version_tag_name:
            logger.info(f"最高バージョンタグを特定: {highest_version_tag_name}")
            return highest_version_tag_name
        logger.warning("有効な最高バージョンタグが見つかりませんでした。")
        return None

    def _parse_github_repo_url(self, url_str: str) -> Optional[Dict[str, str]]:
        """GitHubリポジトリURLからオーナーとリポジトリ名を抽出。"""
        if not url_str:
            logger.warning("リポジトリURLまたはパスが提供されていません。")
            return None

        parsed = urlparse(url_str)
        path_parts = [part for part in parsed.path.strip("/").split("/") if part]  # 空の要素を除去

        owner_candidate: Optional[str] = None
        repo_candidate_raw: Optional[str] = None

        if not parsed.scheme and not parsed.netloc:
            if len(path_parts) == 2:
                owner_candidate, repo_candidate_raw = path_parts[0], path_parts[1]
        elif parsed.netloc.lower() == "github.com":
            if len(path_parts) >= 2:
                owner_candidate, repo_candidate_raw = path_parts[0], path_parts[1]

        if not owner_candidate or not repo_candidate_raw:
            logger.warning(f"無効なGitHubリポジトリURLまたはパス形式: {url_str}")
            return None

        repo = repo_candidate_raw[:-4] if repo_candidate_raw.lower().endswith(".git") else repo_candidate_raw

        if not owner_candidate or not repo:
            logger.warning(f"オーナー名またはリポジトリ名が抽出できませんでした: {url_str}")
            return None

        logger.debug(f"GitHub URL/パス パース成功: owner={owner_candidate}, repo={repo}")
        return {"owner": owner_candidate, "repo": repo}

    def _fetch_latest_release_page_html(self, owner: str, repo: str) -> Optional[str]:
        """最新リリースページのHTMLを取得。"""
        target_url = f"{self.BASE_URL}/{owner}/{repo}/releases/latest"
        logger.debug(f"最新リリースページHTML取得開始: {target_url}")
        try:
            response = requests.get(target_url, allow_redirects=True, timeout=10)  # タイムアウト10秒
            response.raise_for_status()  # HTTPエラーで例外発生
            logger.info(f"最新リリースページHTML取得成功 (最終URL: {response.url})")
            return response.text
        except requests.exceptions.Timeout:
            logger.error(f"最新リリースページHTML取得タイムアウト: {target_url}", exc_info=False)
        except requests.exceptions.ConnectionError:
            logger.error(f"最新リリースページHTML取得接続エラー: {target_url}", exc_info=False)
        except requests.exceptions.HTTPError as e:  # 4xx, 5xx エラー
            logger.error(f"最新リリースページHTML取得HTTPエラー ({e.response.status_code}): {target_url}", exc_info=False)
        except requests.exceptions.RequestException as e:  # 上記以外のrequests関連エラー
            logger.error(f"最新リリースページHTML取得リクエストエラー: {target_url}, {e}", exc_info=True)
        return None

    def get_latest_release_tag(self, repo_url_or_path: str) -> Optional[str]:
        """最新リリースのタグ名を取得。"""
        logger.info(f"最新リリースタグ取得処理開始: {repo_url_or_path}")
        repo_info = self._parse_github_repo_url(repo_url_or_path)
        if not repo_info:
            return None

        html_content = self._fetch_latest_release_page_html(repo_info["owner"], repo_info["repo"])
        if not html_content:
            return None

        soup = BeautifulSoup(html_content, "html.parser")
        # リリースタグへのリンクを探す (GitHubのHTML構造に依存)
        # より堅牢なのは、リダイレクト後のURLから直接タグを抜き出す方法だが、ここではスクレイピングを維持
        # 例: <a href="/owner/repo/releases/tag/v1.0.0" ...>
        tag_link_pattern = re.compile(rf"/{repo_info['owner']}/{repo_info['repo']}/releases/tag/([^/\s]+)")
        tag_link_element = soup.find("a", href=tag_link_pattern)

        if tag_link_element and (href_value := tag_link_element.get("href")) and isinstance(href_value, str):
            if match := tag_link_pattern.search(href_value):  # パターンで再度検索してグループ取得
                tag_name = match.group(1)
                logger.info(f"最新リリースタグ取得成功: {tag_name} (リポジトリ: {repo_info['owner']}/{repo_info['repo']})")
                return tag_name
        logger.warning(f"最新リリースタグが見つかりませんでした ({repo_info['owner']}/{repo_info['repo']})。HTML構造変更の可能性あり。")
        return None

    def get_all_releases_info(self, repo_url_or_path: str) -> Optional[List[Dict[str, Any]]]:
        """指定リポジトリの全てのリリース情報を取得します。"""
        logger.info(f"全リリース情報取得処理開始: {repo_url_or_path}")
        repo_info = self._parse_github_repo_url(repo_url_or_path)
        if not repo_info:
            return None

        owner = repo_info["owner"]
        repo = repo_info["repo"]
        releases_url = f"{self.BASE_URL}/{owner}/{repo}/releases"
        logger.debug(f"リリース一覧ページURL: {releases_url}")

        try:
            response = requests.get(releases_url, allow_redirects=True, timeout=10)
            response.raise_for_status()

            soup = BeautifulSoup(response.text, "html.parser")
            releases_info: List[Dict[str, Any]] = []

            # TODO:GitHubのHTML構造は頻繁に変更されるため、このセレクタは将来的に調整が必要になる可能性があります。
            # 一般的なリリースリストの構造と、提供されたHTMLスニペットの構造を考慮に入れます。
            primary_selector = (
                # 一般的な構造: section > ol/ul > li
                "div.repository-content section[aria-labelledby='releases-label'] ol > li,"
                "div.repository-content section[aria-labelledby='releases-label'] ul > li,"
                # 提供されたHTMLの構造に近いもの: 各リリースが section または section内のdiv.Box に対応
                "div.repository-content section[aria-labelledby='releases-label'] div.Box,"  # 最新リリースセクション内のBox
                "div.repository-content section[aria-labelledby^='hd-']"  # 後続のリリースセクション
            )
            release_elements = soup.select(primary_selector)

            if not release_elements:
                # 上記セレクタで取得できない場合、より広範なセレクタで試行
                # 各リリース情報が <div class="col-md-9"> <div class="Box"> 内にあることを想定
                logger.debug("プライマリセレクタでリリース要素が見つかりませんでした。代替セレクタを試行します。")
                release_elements = soup.select("div.repository-content div.col-md-9 > div.Box")

            if not release_elements:
                logger.warning(f"リリース要素が見つかりませんでした ({owner}/{repo}/releases)。" "HTML構造が変更されたか、リリースが存在しないか、あるいはページの構造が想定と異なる可能性があります。")
                return None

            for release_element in release_elements:
                # タグ名とURLの取得
                # 優先度順: 1. h2 > a, 2. div.flex-1 > span.f1 > a (スニペットの構造), 3. 一般的な a.Link--primary
                tag_name_anchor = release_element.select_one("h2 a.Link--primary")
                if not tag_name_anchor:
                    tag_name_anchor = release_element.select_one("div.flex-1 span.f1.text-bold a.Link--primary")
                if not tag_name_anchor:  # より一般的なフォールバック
                    tag_name_anchor = release_element.select_one("a.Link--primary[href*='/releases/tag/']")

                if not tag_name_anchor:
                    logger.debug("タグ名アンカー要素が見つかりませんでした。このリリース項目をスキップします。")
                    continue

                tag_name = tag_name_anchor.get_text(strip=True)
                if not tag_name:
                    logger.debug("タグ名が空でした。このリリース項目をスキップします。")
                    continue

                href_attribute = tag_name_anchor.get("href")
                html_url = urljoin(self.BASE_URL, href_attribute) if href_attribute else ""

                # "Latest" バッジの確認
                is_latest = False
                # タグ名アンカーの親要素の近く、またはリリース要素全体で探す
                latest_badge_container = tag_name_anchor.parent.parent  # 例: span.f1 の親 div.flex-1
                if latest_badge_container:
                    latest_badge = latest_badge_container.select_one("span.Label.Label--success")

                if not latest_badge:  # release_element全体から探す
                    latest_badge = release_element.select_one("span.Label.Label--success")

                if latest_badge:
                    badge_text = latest_badge.get_text(strip=True).lower()
                    if "latest" in badge_text:  # "Latest release", "Latest" などに対応
                        is_latest = True

                releases_info.append(
                    {
                        "tag_name": tag_name,
                        "is_latest": is_latest,
                        "html_url": html_url,
                    }
                )
                logger.debug(f"リリース情報検出: タグ '{tag_name}', 最新: {is_latest}, URL: {html_url}")

            if not releases_info:
                logger.warning(f"最終的に有効なリリース情報が検出されませんでした ({owner}/{repo}/releases)。")
                return None

            logger.info(f"{len(releases_info)}件のリリース情報を取得しました: {owner}/{repo}")
            return releases_info

        except requests.exceptions.Timeout:
            logger.error(f"リリース一覧ページ取得タイムアウト: {releases_url}", exc_info=False)
            return None
        except requests.exceptions.ConnectionError:
            logger.error(f"リリース一覧ページ取得接続エラー: {releases_url}", exc_info=False)
            return None
        except requests.exceptions.HTTPError as e:
            status_code = e.response.status_code if e.response is not None else "N/A"
            logger.error(f"リリース一覧ページ取得HTTPエラー ({status_code}): {releases_url}", exc_info=False)
            return None
        except requests.exceptions.RequestException as e:
            logger.error(f"リリース一覧ページ取得リクエストエラー: {releases_url}, {e}", exc_info=True)
            return None
        except Exception as e:
            logger.error(f"リリース情報取得処理中の予期せぬエラー ({owner}/{repo}): {e}", exc_info=True)
            return None


class GitHubResourceManager:
    """GitHubリポジトリのリソース（主にリリースアセット）を管理。"""

    BASE_URL: str = "https://github.com"

    def __init__(self, github_token: Optional[str] = None) -> None:
        self._github_token = github_token
        logger.debug(f"GitHubResourceManager 初期化 (トークン使用: {bool(github_token)})")

    def _get_request_headers(self) -> Dict[str, str]:
        """APIリクエスト用のヘッダー情報を生成。"""
        headers = {"Accept": "application/vnd.github.v3+json"}
        if self._github_token:
            headers["Authorization"] = f"token {self._github_token}"
        return headers

    def _parse_github_repo_url(self, url_str: str) -> Optional[Dict[str, str]]:  # Scraperと重複だが依存回避
        parsed = urlparse(url_str)
        path_parts = parsed.path.strip("/").split("/")
        if (parsed.netloc == "github.com" and len(path_parts) >= 2) or (not parsed.scheme and not parsed.netloc and len(path_parts) == 2):
            return {"owner": path_parts[0], "repo": path_parts[1]}
        logger.warning(f"無効なGitHubリポジトリURL形式 (ResourceManager内): {url_str}")
        return None

    def check_repository_connection(self, repo_url_or_path: str) -> bool:
        """指定リポジトリへの疎通確認 (最新リリースページへのアクセス試行)。"""
        logger.info(f"リポジトリ疎通確認開始: {repo_url_or_path}")
        repo_info = self._parse_github_repo_url(repo_url_or_path)
        if not repo_info:
            return False
        target_url = f"{self.BASE_URL}/{repo_info['owner']}/{repo_info['repo']}/releases/latest"
        try:
            # HEADリクエストで存在とリダイレクトを確認
            response = requests.head(target_url, headers=self._get_request_headers(), allow_redirects=False, timeout=10)
            if response.status_code == 302:  # releases/latest が存在すれば302でリダイレクト
                logger.info(f"リポジトリ疎通確認成功 (latestリリースページリダイレクト確認): {target_url}")
                return True
            # 302以外でも2xxならOKとみなすか、より厳密に302のみを成功とするか
            logger.warning(f"リポジトリ疎通確認: {target_url} - ステータスコード {response.status_code} (期待値302)")
            return response.ok  # 2xx系ならTrue
        except requests.exceptions.Timeout:
            logger.error(f"リポジトリ疎通確認タイムアウト: {target_url}", exc_info=False)
        except requests.exceptions.ConnectionError:
            logger.error(f"リポジトリ疎通確認接続エラー: {target_url}", exc_info=False)
        except requests.exceptions.RequestException as e:  # その他requestsエラー
            logger.error(f"リポジトリ疎通確認リクエストエラー: {target_url}, {e}", exc_info=True)
        return False

    def download_release_asset(self, repo_url_or_path: str, tag_name: str, asset_filename: str, destination_directory: Union[str, Path], progress_callback: Optional[Callable[[int, int], None]] = None) -> str:
        """指定リリースの特定アセットファイルをダウンロード。"""
        dest_dir_p = Path(destination_directory)
        logger.info(f"アセットDL開始: {repo_url_or_path} (タグ:{tag_name},ファイル:{asset_filename}) -> {dest_dir_p}")
        repo_info = self._parse_github_repo_url(repo_url_or_path)
        if not repo_info:
            raise ValueError(f"無効なリポジトリURL/パス: {repo_url_or_path}")

        download_url = urljoin(self.BASE_URL, f"/{repo_info['owner']}/{repo_info['repo']}/releases/download/{tag_name}/{asset_filename}")
        logger.debug(f"アセットダウンロードURL: {download_url}")
        destination_file_path = dest_dir_p / asset_filename
        try:
            response = requests.get(download_url, stream=True, headers=self._get_request_headers(), timeout=60)  # タイムアウト延長
            response.raise_for_status()  # HTTPエラーで例外
            dest_dir_p.mkdir(parents=True, exist_ok=True)  # 保存先ディレクトリ作成
            total_size = int(response.headers.get("content-length", 0))
            downloaded_size = 0
            with open(destination_file_path, "wb") as f:
                for chunk in response.iter_content(chunk_size=8192):  # 8KBチャンク
                    if chunk:  # keep-aliveチャンク除外
                        f.write(chunk)
                        downloaded_size += len(chunk)
                        if progress_callback and total_size > 0:
                            progress_callback(total_size, downloaded_size)
            # content-lengthが0でもダウンロードサイズがあれば完了通知
            if progress_callback and total_size == 0 and downloaded_size > 0:
                progress_callback(downloaded_size, downloaded_size)
            logger.info(f"アセットDL成功: {destination_file_path} (サイズ: {downloaded_size} bytes)")
            return str(destination_file_path)
        except requests.exceptions.HTTPError as e:
            if e.response.status_code == 404:
                err_msg = f"アセット '{asset_filename}' (タグ:{tag_name}) が見つかりません(404)。URL:{download_url}"
                logger.error(err_msg, exc_info=True)  # exc_info=Trueでスタックトレースも
                raise FileNotFoundError(err_msg) from e
            logger.error(f"アセットDL HTTPエラー ({e.response.status_code}): {download_url}", exc_info=True)
            raise
        except requests.exceptions.Timeout:
            logger.error(f"アセットDL タイムアウト: {download_url}", exc_info=True)
            raise
        except requests.exceptions.ConnectionError:
            logger.error(f"アセットDL 接続エラー: {download_url}", exc_info=True)
            raise
        except requests.exceptions.RequestException as e:
            logger.error(f"アセットDL リクエストエラー: {download_url}, {e}", exc_info=True)
            raise
        except (IOError, OSError) as e:  # PermissionErrorなど含む
            logger.error(f"アセットDL ファイル書き込みエラー: {destination_file_path}, {e}", exc_info=True)
            raise


class VersionInfo:
    """バージョン情報を保持するデータクラス。"""

    def __init__(self, current: str, latest_available: Optional[str] = None, server_url: Optional[str] = None):
        self.current: str = current
        self.latest_available: Optional[str] = latest_available if latest_available else current
        self.server_url: Optional[str] = server_url

    @property
    def is_update_available(self) -> bool:
        """
        現在のバージョンと利用可能な最新バージョンが異なるか。
        ダウングレードの場合も更新とみなす。
        """
        try:
            return version.parse(self.latest_available) != version.parse(self.current)
        except version.InvalidVersion:
            logger.warning(f"無効なバージョン文字列のため比較不可: current='{self.current}', latest='{self.latest_available}'")
            return False

    def __str__(self) -> str:
        return f"Current: {self.current}, Latest: {self.latest_available}, Updatable: {self.is_update_available}"


class DownloadWorker(QThread):
    """ファイルダウンロード処理をバックグラウンドスレッドで実行するクラス。"""

    # progress_signal: (現在のファイル名, 現在のファイル番号, ファイル総数, 現在のファイルの総サイズ, 現在のファイルのDL済みサイズ)
    progress_signal = Signal(str, int, int, int, int)
    # finished_signal: (ダウンロードしたファイルのパスリスト, ユーザー定義の完了時コールバック関数)
    finished_signal = Signal(list, object)  # objectはCallableだが型チェッカエラー回避のため緩く
    error_signal = Signal(str)  # エラーメッセージ文字列

    def __init__(self, download_function: Callable, repo_url_or_path: str, tag_name: str, filenames_to_download: List[str], destination_dir: Union[str, Path], on_finished_user_callback: Optional[Callable[[], None]]):
        super().__init__()
        self._download_function, self._repo_url, self._tag_name = download_function, repo_url_or_path, tag_name
        self._filenames, self._destination_dir = filenames_to_download, Path(destination_dir)
        self._on_finished_user_callback = on_finished_user_callback
        self._is_cancelled: bool = False
        logger.debug("DownloadWorker 初期化完了")

    def run(self) -> None:
        """ダウンロード処理をスレッドで実行します。"""
        logger.info(f"DownloadWorker 実行開始: {len(self._filenames)}個のファイルをDL (宛先: {self._destination_dir})")
        downloaded_file_paths: List[str] = []
        total_files = len(self._filenames)
        try:
            for i, filename in enumerate(self._filenames):
                if self._is_cancelled:
                    logger.info("ダウンロード処理がキャンセルされました。")
                    self.error_signal.emit("ダウンロードがキャンセルされました。")
                    return

                logger.info(f"ファイルダウンロード開始 ({i+1}/{total_files}): {filename}")

                def _file_progress_callback(total_size: int, downloaded_size: int):
                    if not self._is_cancelled:
                        self.progress_signal.emit(filename, i + 1, total_files, total_size, downloaded_size)

                file_path = self._download_function(self._repo_url, self._tag_name, filename, str(self._destination_dir), _file_progress_callback)
                downloaded_file_paths.append(file_path)
                logger.info(f"ファイルダウンロード完了 ({i+1}/{total_files}): {file_path}")

            if not self._is_cancelled:
                logger.info("全てのファイルのダウンロードが完了しました。")
                self.finished_signal.emit(downloaded_file_paths, self._on_finished_user_callback)
        except Exception as e:  # download_function内で発生しうる全ての例外をキャッチ
            logger.error(f"DownloadWorker でエラー発生: {e}", exc_info=True)
            if not self._is_cancelled:
                self.error_signal.emit(f"ダウンロード中にエラーが発生しました: {type(e).__name__} - {e}")
        finally:
            logger.debug("DownloadWorker スレッド処理終了")  # 成功・失敗・キャンセル問わずログ

    def cancel_download(self) -> None:
        """ダウンロード処理のキャンセルを要求します。"""
        logger.info("DownloadWorker: キャンセル要求受信")
        self._is_cancelled = True


# ----------------------------------------------------------------------
# 6. UI関連クラス
# ----------------------------------------------------------------------
class ButtonGlowAnimator(QObject):
    def __init__(self, target_button: QPushButton, parent: QObject = None):
        super().__init__(parent)
        self._button = target_button
        self._glow_effect = QGraphicsDropShadowEffect(self._button)
        self._glow_effect.setOffset(0, 0)
        self._glow_effect.setBlurRadius(0)
        self._glow_effect.setColor(QColor(0, 0, 0, 0))

        self._button.setGraphicsEffect(self._glow_effect)

        self._animation = QPropertyAnimation(self._glow_effect, b"blurRadius", self)
        self.current_glow_color = QColor(255, 255, 0)
        self._fade_out_animation: Optional[QPropertyAnimation] = None

    def start_glow(self, max_blur_radius: int = 25, duration_ms: int = 1500, easing_curve_type: QEasingCurve.Type = QEasingCurve.Type.InOutSine, alpha_start: int = 0, alpha_end: int = 120):
        if self._fade_out_animation and self._fade_out_animation.state() == QAbstractAnimation.State.Running:
            self._fade_out_animation.stop()

        if self._animation.state() == QAbstractAnimation.State.Running:
            self._animation.stop()

        self._animation.setStartValue(self._glow_effect.blurRadius())
        self._animation.setEndValue(max_blur_radius)
        self._animation.setDuration(duration_ms)
        self._animation.setEasingCurve(QEasingCurve(easing_curve_type))
        self._animation.setLoopCount(-1)

        self._set_effect_color(alpha_start)
        self._glow_effect.setColor(QColor(self.current_glow_color.red(), self.current_glow_color.green(), self.current_glow_color.blue(), alpha_end))

        self._animation.start()

    def stop_glow(self, reset_to_initial: bool = True, duration_ms: int = 200):
        if self._animation.state() == QAbstractAnimation.State.Running:
            self._animation.stop()

            self._fade_out_animation = QPropertyAnimation(self._glow_effect, b"blurRadius", self)
            self._fade_out_animation.setStartValue(self._glow_effect.blurRadius())
            self._fade_out_animation.setEndValue(0)
            self._fade_out_animation.setDuration(duration_ms)
            self._fade_out_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
            self._fade_out_animation.finished.connect(lambda: self._set_effect_color(0))
            self._fade_out_animation.start()
        else:
            if self._fade_out_animation and self._fade_out_animation.state() == QAbstractAnimation.State.Running:
                self._fade_out_animation.stop()

        if reset_to_initial:
            self._set_effect_color(0)
            self._glow_effect.setBlurRadius(0)

    def _set_effect_color(self, alpha: int):
        current_color = self._glow_effect.color()
        new_color = QColor(current_color.red(), current_color.green(), current_color.blue(), alpha)
        self._glow_effect.setColor(new_color)

    def set_glow_color(self, color: QColor):
        self.current_glow_color = color
        if self._animation.state() != QAbstractAnimation.State.Running:
            self._glow_effect.setColor(QColor(color.red(), color.green(), color.blue(), 0))


class HelpPopup(QWidget):
    """ヘルプ情報を表示するポップアップウィンドウ。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("ヘルプ - PS2JPMod")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(540, 360)
        help_text = (
            "このツールは、PCゲーム「PlanetSide 2」の日本語化を支援します。\n\n"
            "**主な使い方**\n"
            "1.  **ゲームフォルダ位置指定 (初回または変更時):**\n"
            "    メイン画面右上の(≡)アイコンから設定画面を開き、「PlanetSide 2 インストールフォルダ」を指定してください。\n"
            "    (例: `C:/Program Files (x86)/Steam/steamapps/common/PlanetSide 2`)\n"
            "2.  **起動モード選択:**\n"
            "    「通常起動」または「Steam起動」を選択します。\n"
            "3.  **ゲーム起動:**\n"
            "    「1:ゲーム起動」ボタンをクリックして、PlanetSide 2のランチャーを起動します。\n"
            "4.  **日本語化適用:**\n"
            "    ランチャーのダウンロード/アップデートゲージ（緑色のバー）が完全に満タンになったことを確認してから、\n    「2:日本語化」ボタンをクリックしてください。これにより、日本語ファイルがゲームに適用されます。\n"
            "5.  **アップデート確認:**\n"
            "    「アップデート確認」ボタンで、このツール本体と日本語翻訳データの更新を確認できます。\n    更新がある場合は、隣に「更新」ボタンが表示されます。\n\n"
            "**その他**\n"
            "-   設定画面では、アップデート情報を取得するサーバーのURLも変更可能です。（通常は変更不要）\n"
            "-   より詳細な情報やトラブルシューティングは、ツールに同梱の「はじめにお読みください.txt」\n    または、GitHubリポジトリのREADMEドキュメントをご参照ください。\n"
        )
        label_help = QLabel(help_text)
        label_help.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label_help.setWordWrap(True)
        label_help.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)  # テキスト選択可能に
        layout = QVBoxLayout(self)
        layout.addWidget(label_help)
        self.setLayout(layout)
        logger.debug("HelpPopup 初期化完了")


class TutorialPopup(QWidget):
    """初回起動時に表示されるチュートリアルとローカルパス設定のポップアップ。"""

    def __init__(self, ui_manager_instance: "UIManager", parent: Optional[QWidget] = None):  # UIManagerを前方参照型指定
        super().__init__(parent)
        self._ui_manager = ui_manager_instance
        self.setWindowTitle("ようこそ！PlanetSide 2 日本語化MODへ")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(510, 280)
        tutorial_text = (
            "PlanetSide 2 日本語化MODをご利用いただきありがとうございます！\n\n"
            "このツールを快適にご利用いただくために、\n最初にPlanetSide 2がインストールされているフォルダを指定してください。\n\n"
            "**一般的なインストール先:**\n"
            "-   Steam版: `C:/Program Files (x86)/Steam/steamapps/common/PlanetSide 2`\n"
            "-   Daybreak Gamesランチャー版: インストール時に指定した場所\n\n"
            "下の入力欄にフォルダパスを入力するか、「参照...」ボタンで選択してください。"
        )
        label_tutorial = QLabel(tutorial_text)
        label_tutorial.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label_tutorial.setWordWrap(True)
        label_tutorial.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setWidget(label_tutorial)

        self._close_glow_animator: Optional[ButtonGlowAnimator] = None
        self._browse_glow_animator: Optional[ButtonGlowAnimator] = None

        button_close = QPushButton("設定を保存して閉じる")
        button_close.clicked.connect(self.close)  # QWidget.close()
        self._close_glow_animator = ButtonGlowAnimator(button_close, self)
        self._close_glow_animator.set_glow_color(QColor(0, 255, 0))  # 淡い緑色に設定

        self.lineedit_local_path_input = QLineEdit()
        self.lineedit_local_path_input.setPlaceholderText("例: C:/Program Files (x86)/Steam/steamapps/common/PlanetSide 2")
        button_browse = QPushButton("参照...")
        self._browse_glow_animator = ButtonGlowAnimator(button_browse, self)
        self._browse_glow_animator.set_glow_color(QColor(0, 255, 0))  # 淡い緑色に設定
        self._browse_glow_animator.start_glow()
        button_browse.clicked.connect(self._browse_for_local_path)

        # textEditedだと編集中に頻繁に発火するため、editingFinishedで入力完了後に通知
        def _on_editing_finished():
            self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_LOCAL_PATH, self.lineedit_local_path_input.text())
            self._browse_glow_animator.stop_glow()
            self._close_glow_animator.start_glow()

        self.lineedit_local_path_input.editingFinished.connect(_on_editing_finished)

        path_group_box = QGroupBox("PlanetSide 2 インストールフォルダ設定")
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.lineedit_local_path_input, 1)  # stretch factor 1で広がるように
        path_layout.addWidget(button_browse)
        path_group_box.setLayout(path_layout)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(scroll_area)
        main_layout.addWidget(path_group_box)
        main_layout.addWidget(button_close, alignment=Qt.AlignmentFlag.AlignCenter)
        self.setLayout(main_layout)
        logger.debug("TutorialPopup 初期化完了")

    def _browse_for_local_path(self) -> None:
        """「参照...」ボタンクリック時の処理。フォルダ選択ダイアログを表示。"""
        logger.debug("チュートリアル: ローカルパス参照ダイアログ表示")
        current_path_str = self.lineedit_local_path_input.text()
        initial_dir = current_path_str if Path(current_path_str).is_dir() else str(Path.home())

        selected_directory = QFileDialog.getExistingDirectory(self, "PlanetSide 2 のインストールフォルダを選択してください", initial_dir)
        if selected_directory:
            logger.info(f"チュートリアル: ローカルパス選択 - {selected_directory}")
            self.lineedit_local_path_input.setText(selected_directory)
            self._browse_glow_animator.stop_glow()
            self._close_glow_animator.start_glow()
            # 即時反映のために UIManager 経由で設定 (editingFinishedを待たない)
            self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_LOCAL_PATH, selected_directory)

    def set_initial_local_path(self, path: str) -> None:
        """ポップアップ表示時に初期ローカルパスを設定。"""
        self.lineedit_local_path_input.setText(path)

    def closeEvent(self, event) -> None:
        """ウィンドウが閉じられるときのイベント。"""
        logger.info("チュートリアルポップアップが閉じられました。")
        event.accept()


class SettingsPopup(QWidget):
    """設定項目を編集するポップアップウィンドウ。"""

    def __init__(self, ui_manager_instance: "UIManager", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self._ui_manager = ui_manager_instance
        self.setWindowTitle("設定 - PS2JPMod")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(520, 350)  # サイズ微調整
        self._init_ui_elements()
        self._setup_layout()
        self._connect_signals()
        logger.debug("SettingsPopup 初期化完了")

    def _init_ui_elements(self) -> None:
        """UI要素（ラベル、入力欄、ボタンなど）を初期化。"""
        self.lineedit_local_path_input = QLineEdit()
        self.lineedit_local_path_input.setPlaceholderText("例: C:/Program Files (x86)/Steam/steamapps/common/PlanetSide 2")
        self.button_browse_local_path = QPushButton("参照...")

        self.lineedit_app_server_url_input = QLineEdit()
        self.lineedit_app_server_url_input.setPlaceholderText("owner_name/repository_name")
        self.lineedit_translation_server_url_input = QLineEdit()
        self.lineedit_translation_server_url_input.setPlaceholderText("owner_name/repository_name")

        self.checkbox_developer_mode = QCheckBox(self)

        self.label_author_credit = QLabel("<b>開発チーム:</b> nusashi (UI), seigo2016 (Core Logic), mossy (翻訳), ru_i (翻訳)")
        self.label_author_credit.setWordWrap(True)
        self.label_license_info = QLabel("<b>ライセンス:</b> ツール本体 (MIT License), 翻訳データ (CC0), 使用フォント (IPAフォントライセンス準拠)")
        self.label_license_info.setWordWrap(True)
        self.label_copyright = QLabel("© 2025 PlanetSide2 日本語化MOD 開発チーム")

    def _setup_layout(self) -> None:
        """UI要素をレイアウトに配置。"""
        gbox_local_path = QGroupBox("ゲームインストールフォルダ設定")
        path_layout = QGridLayout()  # QHBoxより柔軟性を持たせる
        path_layout.addWidget(QLabel("PlanetSide 2 インストールフォルダ:"), 0, 0)
        path_layout.addWidget(self.lineedit_local_path_input, 0, 1)
        path_layout.addWidget(self.button_browse_local_path, 0, 2)
        path_layout.setColumnStretch(1, 1)  # 入力欄が広がるように
        gbox_local_path.setLayout(path_layout)

        gbox_server_url = QGroupBox("開発者用：アップデートサーバー設定 (GitHubリポジトリ: owner/repo 形式)")
        server_url_layout = QGridLayout()
        server_url_layout.addWidget(QLabel("アプリケーション更新サーバー:"), 0, 0)
        server_url_layout.addWidget(self.lineedit_app_server_url_input, 0, 1)
        server_url_layout.addWidget(QLabel("翻訳データ更新サーバー:"), 1, 0)
        server_url_layout.addWidget(self.lineedit_translation_server_url_input, 1, 1)
        server_url_layout.addWidget(QLabel("開発者モード:"), 2, 0)
        server_url_layout.addWidget(self.checkbox_developer_mode, 2, 1)
        server_url_layout.setColumnStretch(1, 1)
        gbox_server_url.setLayout(server_url_layout)

        credits_group_box = QGroupBox("クレジットとライセンス")
        credits_layout = QVBoxLayout()
        credits_layout.addWidget(self.label_author_credit)
        credits_layout.addWidget(self.label_license_info)
        credits_layout.addWidget(self.label_copyright, alignment=Qt.AlignmentFlag.AlignRight)
        credits_group_box.setLayout(credits_layout)

        main_layout = QVBoxLayout(self)
        main_layout.addWidget(gbox_local_path)
        main_layout.addWidget(gbox_server_url)
        main_layout.addStretch(1)  # スペーサー
        main_layout.addWidget(credits_group_box)
        self.setLayout(main_layout)

    def _connect_signals(self) -> None:
        """UI要素のシグナルをスロットに接続。"""
        self.button_browse_local_path.clicked.connect(self._on_browse_local_path_clicked)
        # editingFinishedシグナルで入力完了時に値をMainManagerに反映
        self.lineedit_local_path_input.editingFinished.connect(lambda: self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_LOCAL_PATH, self.lineedit_local_path_input.text()))
        self.lineedit_app_server_url_input.editingFinished.connect(lambda: self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL, self.lineedit_app_server_url_input.text()))
        self.lineedit_translation_server_url_input.editingFinished.connect(lambda: self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL, self.lineedit_translation_server_url_input.text()))
        self.checkbox_developer_mode.stateChanged.connect(lambda: self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_DEVELOPER_MODE, self.checkbox_developer_mode.isChecked()))

    def _on_browse_local_path_clicked(self) -> None:
        """「参照...」ボタンクリック時の処理。"""
        logger.debug("設定画面: ローカルパス参照ダイアログ表示")
        current_path_str = self.lineedit_local_path_input.text()
        initial_dir = current_path_str if Path(current_path_str).is_dir() else str(Path.home())
        selected_directory = QFileDialog.getExistingDirectory(self, "PlanetSide 2 のインストールフォルダを選択してください", initial_dir)
        if selected_directory:
            logger.info(f"設定画面: ローカルパス選択 - {selected_directory}")
            self.lineedit_local_path_input.setText(selected_directory)
            self._ui_manager.set_property_value_by_name(CONST.CONFIG_KEY_LOCAL_PATH, selected_directory)  # 即時反映

    def load_settings_values(self) -> None:
        """現在の設定値をUI要素に読み込みます。ポップアップ表示時に呼び出される。"""
        logger.debug("設定ポップアップ: 設定値をUIに読み込み")
        self.lineedit_local_path_input.setText(self._ui_manager.get_property_value_by_name(CONST.CONFIG_KEY_LOCAL_PATH, CONST.DEFAULT_LOCAL_PATH))
        self.lineedit_app_server_url_input.setText(self._ui_manager.get_property_value_by_name(CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL, CONST.DEFAULT_APP_UPDATE_SERVER_URL))
        self.lineedit_translation_server_url_input.setText(self._ui_manager.get_property_value_by_name(CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL, CONST.DEFAULT_TRANSLATION_UPDATE_SERVER_URL))
        self.checkbox_developer_mode.setChecked(self._ui_manager.get_property_value_by_name(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE))

    def closeEvent(self, event) -> None:
        """ウィンドウが閉じられるときのイベント。"""
        logger.info("設定ポップアップが閉じられました。")
        self._ui_manager.handle_developer_mode_changed_on_settings_close()  # developer_mode変更チェックをトリガー
        self._ui_manager.redraw_main_window_if_needed()
        event.accept()


class TipsPopup(QWidget):
    """Tips情報を表示するポップアップウィンドウ。"""

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Tips - PS2JPMod")
        self.setWindowFlags(self.windowFlags() & ~Qt.WindowMaximizeButtonHint | Qt.WindowCloseButtonHint)
        self.setFixedSize(510, 360)
        tips_text = (
            "**PlanetSide 2 Tips**\n\n"
            "日本語化で、より遊びやすくなった戦場へようこそ！\n\n"
            "-   **ゲーム内の疑問はまず『コーデックス』で！**\n"
            "    ESCメニュー右下にあり、装備やシステムに関する基本情報が載っています。\n\n"
            "-   **もっと詳しい情報や戦略を知りたい時は？**\n"
            "    Webで「PlanetSide 2 Wiki 日本語」などを検索してみましょう！\n"
            "    先人たちの知恵が見つかるかもしれません！\n\n"
            "-   **仲間と連携して戦いたい！**\n"
            "    Discordなどで活動している日本のコミュニティを探してみませんか？分隊行動は勝利への鍵です！\n\n"
            "-   **あなたの戦いを世界へ！配信や動画で共有しよう！**\n"
            "    唯一のMMOFPSであるお祭りゲー PlanetSide 2 の魅力を、あなたの視点で発信してみませんか？\n"
            "    ゲーム配信やプレイ動画の作成・共有は、Daybreak Gamesの利用規約の範囲で推奨されています。\n"
            "    TwitchやYouTubeなどのパートナープログラムを通じた収益化も可能！\n"
            "    (※ 必ずDaybreak Gamesの利用規約をご確認の上、コンテンツを作成してください)\n\n"
            "日本語化をきっかけに、広大なオーラキシスの戦いを存分にお楽しみください！"
        )
        label_tips = QLabel(tips_text)
        label_tips.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        label_tips.setWordWrap(True)
        label_tips.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout = QVBoxLayout(self)
        layout.addWidget(label_tips)
        self.setLayout(layout)
        logger.debug("TipsPopup 初期化完了")


class MainWindow(QMainWindow):
    """アプリケーションのメインウィンドウ。"""

    def __init__(self, ui_manager_instance: "UIManager") -> None:
        super().__init__()
        self._ui_manager = ui_manager_instance
        self.setWindowTitle(CONST.WINDOW_TITLE)
        # ウィンドウアイコン設定はBASE_DIR確定後が望ましいが、ここではまず試行
        self._app_base_dir = Path(os.environ.get("BASE_DIR", "."))  # 環境変数から取得、なければカレント
        self._set_window_icon()
        self._init_ui_elements()
        self._setup_main_layout()
        self.setFixedSize(310, 360)

        try:  # メインディスプレイの左上に配置 (少しオフセット)
            screen_geometry = QApplication.primaryScreen().geometry()
            self.move(screen_geometry.x() + 100, screen_geometry.y() + 100)
        except AttributeError:  # テスト環境などでprimaryScreen()がNoneの場合
            logger.warning("プライマリスクリーンのジオメトリ取得に失敗。デフォルト位置に表示します。")
            self.move(100, 100)

        self._connect_ui_callbacks()
        logger.info("MainWindow 初期化完了")

    def _set_window_icon(self) -> None:
        """ウィンドウアイコンとシステムトレイアイコンを設定。"""
        logger.debug("ウィンドウアイコン設定開始")
        # get_icon_pathユーティリティ関数を使用
        icon_file_path = get_icon_path(self._app_base_dir)
        logger.debug(f"使用するアイコンパス: {icon_file_path}")

        if icon_file_path.exists():
            self.app_icon = QIcon(str(icon_file_path))
            if not self.app_icon.isNull():
                self.setWindowIcon(self.app_icon)
                logger.info(f"ウィンドウアイコン設定成功: {icon_file_path}")
                if QSystemTrayIcon.isSystemTrayAvailable():
                    self.tray_icon = QSystemTrayIcon(self.app_icon, self)  # アイコンを直接コンストラクタに渡す
                    tray_menu = QMenu(self)
                    show_action = tray_menu.addAction("表示")
                    show_action.triggered.connect(self.showNormal)  # showNormalで最小化からも復帰
                    quit_action = tray_menu.addAction("終了")
                    quit_action.triggered.connect(QApplication.instance().quit)
                    self.tray_icon.setContextMenu(tray_menu)
                    self.tray_icon.show()
                    logger.info("システムトレイアイコン設定成功")
                else:
                    logger.info("システムトレイは利用できません。")
            else:
                logger.warning(f"アイコンファイルは存在しますが、QIconオブジェクトの作成に失敗しました: {icon_file_path}")
        else:
            logger.warning(f"アイコンファイルが見つかりません: {icon_file_path}。デフォルトアイコンが使用されます。")

    def _init_ui_elements(self) -> None:
        """UI要素を初期化。"""
        self.radio_button_normal_launch = QRadioButton("通常起動")
        self.radio_button_steam_launch = QRadioButton("Steam起動")

        self.button_launch_game = QPushButton(CONST.BUTTON_TEXT_LAUNCH_GAME)
        self.button_launch_game.setFixedHeight(self.button_launch_game.sizeHint().height() * 2)  # ボタン高さを2倍に
        self.button_apply_translation = QPushButton(CONST.BUTTON_TEXT_APPLY_TRANSLATION)
        self.button_apply_translation.setFixedHeight(self.button_apply_translation.sizeHint().height() * 2)

        self.label_app_version = QLabel("アプリバージョン: -")
        self.button_update_app = QPushButton(CONST.BUTTON_TEXT_UPDATE)
        self.button_update_app.setFixedSize(self.button_update_app.sizeHint())
        self.button_update_app.setVisible(False)

        self.label_translation_version = QLabel("翻訳バージョン: -")
        self.button_update_translation = QPushButton(CONST.BUTTON_TEXT_UPDATE)
        self.button_update_translation.setFixedSize(self.button_update_translation.sizeHint())
        self.button_update_translation.setVisible(False)

        self.button_check_for_updates = QPushButton(CONST.BUTTON_TEXT_CHECK_FOR_UPDATES)

        self.textedit_status_display = QTextEdit()
        self.textedit_status_display.setReadOnly(True)
        self.textedit_status_display.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        font_metrics = QFontMetrics(self.textedit_status_display.font())  # QTextEditのフォントメトリクスを取得
        self.textedit_status_display.setFixedHeight(int(font_metrics.lineSpacing() * CONST.STATUS_DISPLAY_LINE_COUNT))

        def create_tool_button(icon_std_pixmap: QStyle.StandardPixmap, tooltip_text: str) -> QPushButton:
            """ツールバー風ボタンを作成するヘルパー関数。"""
            button = QPushButton()
            button.setIcon(self.style().standardIcon(icon_std_pixmap))
            button.setIconSize(QSize(24, 24))
            button.setFixedSize(30, 30)  # アイコンサイズとボタンサイズ
            button.setStyleSheet("QPushButton { background-color: transparent; border: none; }")  # 透明背景、枠なし
            button.setToolTip(tooltip_text)  # マウスオーバー時のツールチップ
            return button

        self.button_show_tips_popup = create_tool_button(QStyle.StandardPixmap.SP_FileDialogInfoView, "ヒント")
        self.button_show_settings_popup = create_tool_button(QStyle.StandardPixmap.SP_FileDialogDetailedView, "設定 (≡)")
        self.button_show_help_popup = create_tool_button(QStyle.StandardPixmap.SP_MessageBoxQuestion, "ヘルプ (使い方)")

    def _setup_main_layout(self) -> None:
        """UI要素をメインウィンドウのレイアウトに配置。"""
        # 起動モードグループ
        gbox_launch_mode = QGroupBox("起動モード選択")
        hbox_launch = QHBoxLayout()
        hbox_launch.addWidget(self.radio_button_normal_launch)
        hbox_launch.addWidget(self.radio_button_steam_launch)
        gbox_launch_mode.setLayout(hbox_launch)

        # ヘッダー部分 (起動モードとツールボタン)
        header_layout = QHBoxLayout()
        header_layout.addWidget(gbox_launch_mode)  # 左側
        header_layout.addStretch(1)  # 中央のスペーサー
        header_layout.addWidget(self.button_show_tips_popup)
        header_layout.addWidget(self.button_show_help_popup)  # 右側
        header_layout.addWidget(self.button_show_settings_popup)

        # メイン操作ボタン
        main_action_buttons_layout = QHBoxLayout()
        main_action_buttons_layout.addWidget(self.button_launch_game)
        main_action_buttons_layout.addWidget(self.button_apply_translation)

        # バージョン情報と更新グループ
        gbox_version_info = QGroupBox("バージョン情報と更新")
        grid_version_layout = QGridLayout()
        grid_version_layout.addWidget(self.label_app_version, 0, 0)
        grid_version_layout.addWidget(self.button_update_app, 0, 1, Qt.AlignmentFlag.AlignRight)  # ボタンを右寄せ
        grid_version_layout.addWidget(self.label_translation_version, 1, 0)
        grid_version_layout.addWidget(self.button_update_translation, 1, 1, Qt.AlignmentFlag.AlignRight)
        grid_version_layout.addWidget(self.button_check_for_updates, 2, 0, 1, 2)  # ボタンを2列にまたがせる
        grid_version_layout.setColumnStretch(0, 1)  # 0列目(ラベル側)を優先的に広げる
        gbox_version_info.setLayout(grid_version_layout)

        # ステータス表示グループ
        gbox_status_display = QGroupBox("ステータス")
        vbox_status_layout = QVBoxLayout()
        vbox_status_layout.addWidget(self.textedit_status_display)
        gbox_status_display.setLayout(vbox_status_layout)

        # 全体をまとめるメイン垂直レイアウト
        main_vertical_layout = QVBoxLayout()
        main_vertical_layout.addLayout(header_layout)
        main_vertical_layout.addLayout(main_action_buttons_layout)
        main_vertical_layout.addWidget(gbox_status_display)
        main_vertical_layout.addWidget(gbox_version_info)
        main_vertical_layout.addStretch(1)  # 最下部の余白（可変）

        central_widget = QWidget()
        central_widget.setLayout(main_vertical_layout)
        self.setCentralWidget(central_widget)

    def _connect_ui_callbacks(self) -> None:
        """UI要素のイベントをUIManagerの処理関数に接続。"""
        # ラムダ式でUIManagerのメソッドを呼び出す
        self.radio_button_normal_launch.clicked.connect(lambda: self._ui_manager.handle_launch_mode_changed(LaunchMode.NORMAL))
        self.radio_button_steam_launch.clicked.connect(lambda: self._ui_manager.handle_launch_mode_changed(LaunchMode.STEAM))

        self.button_launch_game.clicked.connect(self._ui_manager.handle_game_launch_button_clicked)
        self.button_apply_translation.clicked.connect(self._ui_manager.handle_apply_translation_button_clicked)
        self.button_update_app.clicked.connect(self._ui_manager.handle_update_app_button_clicked)
        self.button_update_translation.clicked.connect(self._ui_manager.handle_update_translation_button_clicked)
        self.button_check_for_updates.clicked.connect(self._ui_manager.handle_check_for_updates_button_clicked)
        self.button_show_settings_popup.clicked.connect(self._ui_manager.handle_show_settings_popup_clicked)
        self.button_show_tips_popup.clicked.connect(self._ui_manager.handle_show_tips_popup_clicked)
        self.button_show_help_popup.clicked.connect(self._ui_manager.handle_show_help_popup_clicked)

    # --- UI更新用メソッド群 (UIManagerからシグナル経由で呼び出されるスロット) ---
    def update_status_text(self, status_message: str) -> None:
        """ステータス表示欄のテキストを更新。"""
        self.textedit_status_display.setText(status_message)

    def update_app_version_display(self, version_str: str, is_update_available: bool) -> None:
        """アプリのバージョン表示と更新ボタンの可視性を更新。"""
        self.label_app_version.setText(f"アプリバージョン: {version_str}")
        self.button_update_app.setVisible(is_update_available)
        self._ui_manager.set_glow_update_app_button(is_update_available)

    def update_translation_version_display(self, version_str: str, is_update_available: bool) -> None:
        """翻訳データのバージョン表示と更新ボタンの可視性を更新。"""
        self.label_translation_version.setText(f"翻訳バージョン: {version_str}")
        self.button_update_translation.setVisible(is_update_available)
        self._ui_manager.set_glow_update_translation_button(is_update_available)

    def update_launch_mode_selection(self, launch_mode_value: int) -> None:  # intで受け取る
        """起動モードのラジオボタン選択状態を更新。"""
        try:
            mode = LaunchMode(launch_mode_value)  # intからEnumに変換
            if mode == LaunchMode.NORMAL:
                self.radio_button_normal_launch.setChecked(True)
            elif mode == LaunchMode.STEAM:
                self.radio_button_steam_launch.setChecked(True)
            else:
                logger.warning(f"未定義の起動モード値が指定されました: {launch_mode_value}")
                self.radio_button_steam_launch.setChecked(True)  # 不明時はSteam
        except ValueError:
            logger.error(f"起動モードのUI更新時に無効な値を受け取りました: {launch_mode_value}")
            self.radio_button_steam_launch.setChecked(True)

    def closeEvent(self, event) -> None:
        """メインウィンドウが閉じられるときのイベント。"""
        logger.info("MainWindow がユーザーによって閉じられようとしています。")
        if self._ui_manager:
            self._ui_manager.handle_main_window_close_event(event)
        else:  # UIManagerが未設定の異常ケース (通常発生しない)
            logger.critical("UIManagerが未設定の状態でMainWindowが閉じられようとしました。")
            QApplication.instance().quit()
            event.accept()


class UIManager(QObject):
    """UI全体の管理、UIイベント処理、MainManagerとの連携を行うクラス。"""

    # UI更新用シグナル
    status_message_changed_signal = Signal(str)
    app_version_updated_signal = Signal(str, bool)  # (version_string, is_update_available)
    translation_version_updated_signal = Signal(str, bool)  # (version_string, is_update_available)
    launch_mode_ui_update_signal = Signal(int)  # LaunchModeのint値

    def __init__(self, main_manager_instance: "MainManager") -> None:
        super().__init__()
        self._app = QApplication.instance() or QApplication(sys.argv)  # 既存インスタンス利用
        self._main_manager = main_manager_instance
        self._main_window: Optional[MainWindow] = None

        # 各ボタンに対応するButtonGlowAnimatorのインスタンスを保持するメンバー変数
        self._app_update_glow_animator: Optional[ButtonGlowAnimator] = None
        self._translation_update_glow_animator: Optional[ButtonGlowAnimator] = None
        self._launch_game_glow_animator: Optional[ButtonGlowAnimator] = None
        self._apply_translation_glow_animator: Optional[ButtonGlowAnimator] = None

        self._game_launch_timer: Optional[QTimer] = None

        self._settings_popup: Optional[SettingsPopup] = None
        self._help_popup: Optional[HelpPopup] = None
        self._tutorial_popup: Optional[TutorialPopup] = None
        self._tips_popup: Optional[TipsPopup] = None  # 指示④: TipsPopupメンバー変数追加
        self._download_worker: Optional[DownloadWorker] = None
        self._is_download_in_progress: bool = False  # ダウンロード多重実行防止フラグ
        self._property_accessors: Dict[str, Dict[str, Callable]] = {}  # MainManagerプロパティアクセサー
        logger.info("UIManager 初期化")

    def initialize_ui(self) -> None:
        """全てのUIウィンドウを初期化し、シグナル・スロットを接続。"""
        logger.info("UIManager: UI初期化開始")
        self._main_window = MainWindow(self)
        self._settings_popup = SettingsPopup(self)
        self._help_popup = HelpPopup()
        self._tutorial_popup = TutorialPopup(self)
        self._tips_popup = TipsPopup()

        # 各ボタンにButtonGlowAnimatorを初期化し、グロー色を設定
        self._launch_game_glow_animator = ButtonGlowAnimator(self._main_window.button_launch_game, self)
        self._launch_game_glow_animator.set_glow_color(QColor(0, 255, 0))  # 淡い緑色に設定

        self._apply_translation_glow_animator = ButtonGlowAnimator(self._main_window.button_apply_translation, self)
        self._apply_translation_glow_animator.set_glow_color(QColor(255, 0, 0))  # 淡い赤色に設定

        self._app_update_glow_animator = ButtonGlowAnimator(self._main_window.button_update_app, self)
        self._app_update_glow_animator.set_glow_color(QColor(0, 255, 0))  # 淡い緑色に設定

        self._translation_update_glow_animator = ButtonGlowAnimator(self._main_window.button_update_translation, self)
        self._translation_update_glow_animator.set_glow_color(QColor(0, 255, 0))  # 淡い緑色に設定

        # MainWindowが作成された後にシグナルを接続
        if self._main_window:
            self.status_message_changed_signal.connect(self._main_window.update_status_text)
            self.app_version_updated_signal.connect(self._main_window.update_app_version_display)
            self.translation_version_updated_signal.connect(self._main_window.update_translation_version_display)
            self.launch_mode_ui_update_signal.connect(self._main_window.update_launch_mode_selection)
        logger.info("UIManager: UI初期化完了およびシグナル・スロット接続完了")

    def set_glow_launch_game_button(self, is_visible: bool) -> None:
        """ゲーム起動ボタンのグローを設定。"""
        if self._launch_game_glow_animator:
            if is_visible:
                self._launch_game_glow_animator.start_glow()
            else:
                self._launch_game_glow_animator.stop_glow()

    def set_glow_apply_translation_button(self, is_visible: bool) -> None:
        """日本語化ボタンのグローを設定。"""
        if self._apply_translation_glow_animator:
            if is_visible:
                self._apply_translation_glow_animator.start_glow()
            else:
                self._apply_translation_glow_animator.stop_glow()

    def set_glow_update_app_button(self, is_visible: bool) -> None:
        """アプリ更新ボタンのグローを設定。"""
        if self._app_update_glow_animator:
            if is_visible:
                self._app_update_glow_animator.start_glow()
            else:
                self._app_update_glow_animator.stop_glow()

    def set_glow_update_translation_button(self, is_visible: bool) -> None:
        """翻訳更新ボタンのグローを設定。"""
        if self._translation_update_glow_animator:
            if is_visible:
                self._translation_update_glow_animator.start_glow()
            else:
                self._translation_update_glow_animator.stop_glow()

    def show_main_window(self) -> None:
        """メインウィンドウを表示。"""
        if self._main_window:
            logger.info("メインウィンドウ表示")
            self.redraw_main_window_if_needed()
            self._main_window.show()
        else:
            logger.error("メインウィンドウが初期化されていません。表示できません。")

    def run_app_event_loop(self) -> int:
        """Qtアプリケーションのイベントループを開始。"""
        logger.info("Qtアプリケーションイベントループ開始")
        return self._app.exec()

    def register_property_accessor(self, property_name: str, getter: Callable[[], Any], setter: Callable[[Any], None]) -> None:
        """MainManagerのプロパティへのアクセサーを登録。"""
        self._property_accessors[property_name] = {"getter": getter, "setter": setter}
        logger.debug(f"プロパティアクセサー登録: {property_name}")

    def get_property_value_by_name(self, property_name: str, default: Optional[Any] = None) -> Any:
        """登録されたgetter経由でMainManagerのプロパティ値を取得。"""
        if (accessor := self._property_accessors.get(property_name)) and (getter := accessor.get("getter")):
            try:
                return getter()
            except Exception as e:
                logger.error(f"プロパティ '{property_name}' のgetter呼び出し中にエラー: {e}", exc_info=True)
                return default
        logger.warning(f"プロパティ '{property_name}' のgetterが登録されていません。デフォルト値({default})を返します。")
        return default

    def set_property_value_by_name(self, property_name: str, value: Any) -> None:
        """登録されたsetter経由でMainManagerのプロパティ値を設定。"""
        if (accessor := self._property_accessors.get(property_name)) and (setter := accessor.get("setter")):
            try:
                setter(value)
                logger.info(f"プロパティ '{property_name}' に値を設定しました: {value}")
                # 主要な設定変更時はUI再描画をトリガー
                if property_name in [CONST.CONFIG_KEY_LOCAL_PATH, CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL, CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL, CONST.CONFIG_KEY_LAUNCH_MODE]:
                    self.redraw_main_window_if_needed()
            except Exception as e:
                logger.error(f"プロパティ '{property_name}' のsetter呼び出し中にエラー: {e}", exc_info=True)
        else:
            logger.warning(f"プロパティ '{property_name}' のsetterが登録されていません。")

    # --- MainWindowからのイベントハンドラ群 ---
    def handle_launch_mode_changed(self, launch_mode: LaunchMode) -> None:
        logger.info(f"起動モード変更イベント受信: モード = {launch_mode.name}")
        self.set_property_value_by_name(CONST.CONFIG_KEY_LAUNCH_MODE, launch_mode)  # MainManager側でEnumのまま扱う
        self.launch_mode_ui_update_signal.emit(launch_mode.value)  # UIへはint値を渡す
        self.status_message_changed_signal.emit(f"起動モードを「{launch_mode.name}」に設定しました。")

    def handle_game_launch_button_clicked(self) -> None:
        logger.info("「ゲーム起動」ボタンクリックイベント受信")
        self._main_manager.execute_game_launch()
        self.set_glow_apply_translation_button(True)

    def handle_apply_translation_button_clicked(self) -> None:
        logger.info("「日本語化」ボタンクリックイベント受信")
        self._main_manager.execute_translation_apply()
        self.set_glow_apply_translation_button(False)

    def handle_update_app_button_clicked(self) -> None:
        logger.info("「アプリ更新」ボタンクリックイベント受信")
        self._main_manager.execute_app_update_download()

    def handle_update_translation_button_clicked(self) -> None:
        logger.info("「翻訳更新」ボタンクリックイベント受信")
        self._main_manager.execute_translation_update_download()

    def handle_check_for_updates_button_clicked(self) -> None:
        logger.info("「アップデート確認」ボタンクリックイベント受信")
        self.status_message_changed_signal.emit("アップデート情報を確認中...")
        self._main_manager.execute_check_for_updates()

    def handle_show_settings_popup_clicked(self) -> None:
        logger.info("「設定表示」ボタンクリックイベント受信")
        if self._settings_popup:
            self._settings_popup.load_settings_values()
            self._settings_popup.show()
        else:
            logger.error("設定ポップアップが初期化されていません。")

    def handle_show_help_popup_clicked(self) -> None:
        logger.info("「ヘルプ表示」ボタンクリックイベント受信")
        if self._help_popup:
            self._help_popup.show()
        else:
            logger.error("ヘルプポップアップが初期化されていません。")

    def handle_show_tips_popup_clicked(self) -> None:
        logger.info("「Tips表示」ボタンクリックイベント受信")
        if self._tips_popup:
            self._tips_popup.show()
        else:
            logger.error("Tipsポップアップが初期化されていません。")

    def show_tutorial_popup_if_needed(self, is_first_time: bool, current_local_path: str) -> None:
        """初回起動時やローカルパス未設定時にチュートリアルポップアップを表示。"""
        if is_first_time or not current_local_path:  # ローカルパスが空でも表示
            reason = "初回起動" if is_first_time else "ゲームインストールフォルダ未設定"
            logger.info(f"{reason}のため、設定を促すポップアップを表示します。")
            if self._tutorial_popup:
                self._tutorial_popup.set_initial_local_path(current_local_path)  # 現在のパス（空文字列含む）を渡す
                self._tutorial_popup.show()
            else:
                logger.error("チュートリアルポップアップが初期化されていません。")

    def handle_main_window_close_event(self, event) -> None:
        """メインウィンドウのクローズイベントを処理。"""
        logger.info("UIManager: メインウィンドウクローズイベント処理")
        if self._is_download_in_progress and self._download_worker and self._download_worker.isRunning():
            logger.warning("ダウンロード処理が実行中です。終了前にキャンセルしてください。")
            # ここで確認ダイアログを出すことも可能
            # (例: QMessageBox.question(...))
            # 今回はキャンセル要求のみ行い、終了は許可する
            self._download_worker.cancel_download()
        QApplication.instance().quit()  # アプリケーション終了
        event.accept()

    # --- DownloadWorker との連携 ---
    def start_background_download(
        self, download_function: Callable, repo_url: str, tag_name: str, filenames: List[str], destination_dir: Union[str, Path], on_finished_callback: Optional[Callable[[], None]]  # MainManagerの実際のDL関数
    ) -> None:
        """バックグラウンドでのファイルダウンロードを開始。"""
        if self._is_download_in_progress:  # 多重実行防止
            logger.warning("既に別のダウンロード処理が実行中です。")
            self.status_message_changed_signal.emit("エラー: 別のダウンロードが実行中です。完了までお待ちください。")
            return

        logger.info(f"バックグラウンドダウンロード開始: {len(filenames)}個のファイル (リポジトリ: {repo_url}, タグ: {tag_name})")
        self.status_message_changed_signal.emit(f"ダウンロード準備中: {filenames[0]} ...")
        self._is_download_in_progress = True  # フラグを立てる

        self._download_worker = DownloadWorker(download_function, repo_url, tag_name, filenames, destination_dir, on_finished_callback)
        self._download_worker.progress_signal.connect(self._on_download_progress_updated)
        self._download_worker.finished_signal.connect(self._on_download_process_finished)
        self._download_worker.error_signal.connect(self._on_download_process_error)
        self._download_worker.start()

    def _on_download_progress_updated(self, filename: str, current_file_num: int, total_files: int, total_size: int, downloaded_size: int) -> None:
        """ダウンロード進捗の更新を受け取り、UIに通知。"""
        if total_size > 0:
            progress_percent = int((downloaded_size / total_size) * 100)
            status_msg = f"ダウンロード中 ({current_file_num}/{total_files}): {filename}\n" f"({downloaded_size/1024/1024:.2f}MB / {total_size/1024/1024:.2f}MB - {progress_percent}%)"
        else:  # total_size が不明な場合 (GitHubでは通常ありえないが念のため)
            status_msg = f"ダウンロード中 ({current_file_num}/{total_files}): {filename}\n" f"({downloaded_size/1024/1024:.2f}MB)"
        self.status_message_changed_signal.emit(status_msg)

    def _on_download_process_finished(self, downloaded_files: List[str], user_callback: Optional[Callable[[], None]]) -> None:
        """ダウンロード処理全体の完了通知。"""
        logger.info(f"ダウンロード処理完了。ダウンロードファイル数: {len(downloaded_files)}")
        self.status_message_changed_signal.emit(f"ダウンロードが完了しました。 ({len(downloaded_files)}個のファイル)")
        self._is_download_in_progress = False  # フラグを下ろす
        self._download_worker = None  # ワーカー参照をクリア
        if user_callback:
            try:
                logger.debug("ダウンロード完了後ユーザーコールバック実行")
                user_callback()
            except Exception as e:
                logger.error(f"ダウンロード完了後コールバック実行中にエラー: {e}", exc_info=True)
                self.status_message_changed_signal.emit(f"エラー: ダウンロード後処理中に問題が発生しました - {type(e).__name__}")

    def _on_download_process_error(self, error_message: str) -> None:
        """ダウンロード処理中のエラー通知。"""
        logger.error(f"ダウンロード処理エラー: {error_message}")
        self.status_message_changed_signal.emit(f"エラー: {error_message}")
        self._is_download_in_progress = False  # フラグを下ろす
        self._download_worker = None  # ワーカー参照をクリア

    # --- UI再描画関連 ---
    def redraw_main_window_if_needed(self) -> None:
        """MainManagerから取得した最新の状態でMainWindowの関連部分を再描画。"""
        logger.debug("MainWindowの再描画要求")
        if not self._main_window:
            logger.warning("メインウィンドウが未初期化のため再描画できません。")
            return

        self.status_message_changed_signal.emit(self.get_property_value_by_name("status_string_for_ui", "状態不明"))

        app_ver_info: Optional[VersionInfo] = self.get_property_value_by_name("app_version_info")
        self.app_version_updated_signal.emit(app_ver_info.current if app_ver_info else CONST.DEFAULT_APP_VERSION, app_ver_info.is_update_available if app_ver_info else False)

        trans_ver_info: Optional[VersionInfo] = self.get_property_value_by_name("translation_version_info")
        self.translation_version_updated_signal.emit(trans_ver_info.current if trans_ver_info else CONST.DEFAULT_TRANSLATION_VERSION, trans_ver_info.is_update_available if trans_ver_info else False)

        launch_mode: LaunchMode = self.get_property_value_by_name(CONST.CONFIG_KEY_LAUNCH_MODE, CONST.DEFAULT_LAUNCH_MODE)
        self.launch_mode_ui_update_signal.emit(launch_mode.value)  # Enumの値を渡す

        QApplication.processEvents()  # UIの変更を即時反映させる
        logger.debug("MainWindowの再描画完了")

    def handle_developer_mode_changed_on_settings_close(self) -> None:
        """
        設定ポップアップが閉じられた際に、開発者モードの変更を検出し、
        必要に応じてバージョン情報の整合性チェックを再実行します。
        """
        self._main_manager.handle_developer_mode_changed_on_settings_close()


# ----------------------------------------------------------------------
# 7. メイン処理管理クラス
# ----------------------------------------------------------------------
class MainManager:
    """アプリケーション全体の制御、ビジネスロジック、コンポーネント連携を担当。"""

    def __init__(self, data_dir_path: str) -> None:
        logger.info("MainManager 初期化開始")
        self._data_dir: Path = Path(data_dir_path)
        self._status_string_for_ui: str = "初期化中..."

        self._config_manager = JsonConfigManager(str(self._data_dir))
        self._github_resource_manager = GitHubResourceManager()  # トークンは現状未使用
        self._github_scraper = GitHubReleaseScraper()
        # self._file_checker = FileIntegrityChecker() # SHAチェックは現在未使用のためコメントアウト

        # バージョン情報 (最新のチェック結果を保持)
        self._app_version_info = VersionInfo(
            current=self._config_manager.get_config_value(CONST.CONFIG_KEY_APP_VERSION, CONST.DEFAULT_APP_VERSION),
            server_url=self._config_manager.get_config_value(CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL, CONST.DEFAULT_APP_UPDATE_SERVER_URL),
        )
        self._translation_version_info = VersionInfo(
            current=self._config_manager.get_config_value(CONST.CONFIG_KEY_TRANSLATION_VERSION, CONST.DEFAULT_TRANSLATION_VERSION),
            server_url=self._config_manager.get_config_value(CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL, CONST.DEFAULT_TRANSLATION_UPDATE_SERVER_URL),
        )

        self._previous_developer_mode_state: bool = self._config_manager.get_config_value(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE)

        self._ui_manager = UIManager(self)  # UIManagerに自身のインスタンスを渡す
        self._register_properties_with_ui_manager()
        logger.info("MainManager 初期化完了")

    def _register_properties_with_ui_manager(self) -> None:
        """UIManagerに、このクラスの管理するプロパティへのアクセサーを登録。"""
        logger.debug("UIManagerへのプロパティアクセサー登録開始")
        # 読み取り専用プロパティ
        read_only_properties = {
            "status_string_for_ui": lambda: self._status_string_for_ui,
            "app_version_info": lambda: self._app_version_info,
            "translation_version_info": lambda: self._translation_version_info,
        }
        for name, getter in read_only_properties.items():
            self._ui_manager.register_property_accessor(name, getter, lambda val: None)  # setterなし

        # 読み書き可能プロパティ (設定項目)
        configurable_properties = [
            CONST.CONFIG_KEY_LAUNCH_MODE,
            CONST.CONFIG_KEY_LOCAL_PATH,
            CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL,
            CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL,
            CONST.CONFIG_KEY_DEVELOPER_MODE,
        ]
        for prop_name in configurable_properties:
            if prop_name == CONST.CONFIG_KEY_DEVELOPER_MODE:
                # developer_modeのsetterは、変更前の状態を記録するために特別な処理
                def developer_mode_setter(value, p=prop_name):
                    # 新しい値が設定される前に、現在の値をprevious_developer_mode_stateに保存
                    self._previous_developer_mode_state = self._config_manager.get_config_value(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE)
                    self._config_manager.set_config_value(p, value)

                self._ui_manager.register_property_accessor(
                    prop_name,
                    lambda p=prop_name: self._config_manager.get_config_value(p, CONST.DEFAULT_DEVELOPER_MODE),
                    developer_mode_setter,
                )
            else:
                self._ui_manager.register_property_accessor(
                    prop_name,
                    # getter: config_managerから値を取得
                    lambda p=prop_name: self._config_manager.get_config_value(
                        p,
                        (
                            CONST.DEFAULT_LAUNCH_MODE
                            if p == CONST.CONFIG_KEY_LAUNCH_MODE
                            else CONST.DEFAULT_LOCAL_PATH if p == CONST.CONFIG_KEY_LOCAL_PATH else CONST.DEFAULT_APP_UPDATE_SERVER_URL if p == CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL else CONST.DEFAULT_TRANSLATION_UPDATE_SERVER_URL
                        ),
                    ),
                    # setter: config_managerに値を設定
                    lambda value, p=prop_name: self._config_manager.set_config_value(p, value),
                )
        logger.debug("UIManagerへのプロパティアクセサー登録完了")

    def initialize_application_state_and_ui(self) -> None:
        """アプリケーションの状態を初期化し、UIの準備を行う。"""
        logger.info("アプリケーション状態とUIの初期化開始")
        self._ui_manager.initialize_ui()  # UIウィンドウの作成とシグナル接続

        # アプリバージョンが設定ファイルのバージョンより新しい場合は更新
        config_app_ver_str = self._config_manager.get_config_value(CONST.CONFIG_KEY_APP_VERSION, "0.0.0")
        if version.parse(CONST.DEFAULT_APP_VERSION) > version.parse(config_app_ver_str):
            logger.info(f"アプリの組込バージョン({CONST.DEFAULT_APP_VERSION})が設定ファイル({config_app_ver_str})より新しいため、設定を更新します。")
            self._config_manager.set_config_value(CONST.CONFIG_KEY_APP_VERSION, CONST.DEFAULT_APP_VERSION)
            self._app_version_info.current = CONST.DEFAULT_APP_VERSION  # 内部状態も更新

        # 初回起動チェックとチュートリアル表示
        is_first_time = self._config_manager.is_initial_config()
        current_local_path = self._config_manager.get_config_value(CONST.CONFIG_KEY_LOCAL_PATH, "")
        self._ui_manager.show_tutorial_popup_if_needed(is_first_time, current_local_path)

        self.execute_check_for_updates()  # 起動時にアップデート確認 (UI更新も含む)
        self._status_string_for_ui = "日本語化MODの起動が完了しました。"  # execute_check_for_updates の後に設定
        self._ui_manager.show_main_window()  # UI表示
        logger.info("アプリケーション状態とUIの初期化完了")

    def execute_game_launch(self) -> None:
        """ゲームの起動処理を実行。"""
        logger.info("ゲーム起動処理実行")
        launch_mode: LaunchMode = self._config_manager.get_config_value(CONST.CONFIG_KEY_LAUNCH_MODE)
        local_game_path_str: str = self._config_manager.get_config_value(CONST.CONFIG_KEY_LOCAL_PATH)

        if launch_mode == LaunchMode.NORMAL and not local_game_path_str:
            self._status_string_for_ui = "エラー: 「通常起動」モードでは、ゲームのインストールフォルダが設定されている必要があります。設定画面から指定してください。"
            logger.error("通常起動モードでローカルパス未設定のため起動不可。")
        elif launch_mode == LaunchMode.NORMAL:
            launchpad_exe_path = Path(local_game_path_str) / "LaunchPad.exe"
            if launchpad_exe_path.is_file():
                try:
                    subprocess.Popen([str(launchpad_exe_path)], cwd=str(Path(local_game_path_str)))
                    self._status_string_for_ui = "ゲームランチャーを起動しました。\nランチャーの緑ゲージが完全に満タンになったら\n「日本語化」ボタンを押してください。"
                    logger.info(f"LaunchPad.exe を通常起動しました: {launchpad_exe_path}")
                except (FileNotFoundError, PermissionError) as e_os:  # 具体的なOSエラー
                    self._status_string_for_ui = f"エラー: LaunchPad.exe の起動に失敗しました - {type(e_os).__name__}: {e_os}"
                    logger.error(f"LaunchPad.exe の起動失敗 ({type(e_os).__name__}): {e_os}", exc_info=False)  # exc_info=Falseで重複回避
                except Exception as e:  # その他の予期せぬエラー
                    self._status_string_for_ui = f"エラー: LaunchPad.exe の起動中に予期せぬ問題が発生しました - {type(e).__name__}"
                    logger.error(f"LaunchPad.exe 起動中の予期せぬエラー: {e}", exc_info=True)
            else:
                self._status_string_for_ui = f"エラー: 指定された場所に LaunchPad.exe が見つかりません。\nパス: {launchpad_exe_path}"
                logger.error(f"LaunchPad.exe が見つかりません: {launchpad_exe_path}")
        elif launch_mode == LaunchMode.STEAM:
            try:
                os.startfile(CONST.STEAM_GAME_URI)  # Windows固有
                self._status_string_for_ui = "Steam経由でゲームランチャーを起動しました。\nランチャーの緑ゲージが完全に満タンになったら\n「日本語化」ボタンを押してください。"
                logger.info(f"Steam URI を起動しました: {CONST.STEAM_GAME_URI}")
            except Exception as e:  # os.startfileは広範なエラーを出す可能性
                self._status_string_for_ui = f"エラー: Steam経由でのゲーム起動に失敗しました - {type(e).__name__}: {e}"
                logger.error(f"Steam URI ({CONST.STEAM_GAME_URI}) の起動失敗: {e}", exc_info=True)
        else:  # ありえないはずだが念のため
            self._status_string_for_ui = "エラー: 不正な起動モードが選択されています。設定を確認してください。"
            logger.error(f"不正な起動モード値が検出されました: {launch_mode}")
        self._ui_manager.redraw_main_window_if_needed()  # ステータス更新をUIに反映

    def _check_source_files_exist(self, source_files_map: Dict[str, Path]) -> bool:
        """日本語化に必要なコピー元ファイルが存在するかチェック。"""
        for name, path in source_files_map.items():
            if not path.exists():
                self._status_string_for_ui = f"エラー: 日本語化に必要な{name}ファイルが見つかりません。\nパス: {path}\nツールを再ダウンロードするか、ファイルを確認してください。"
                logger.error(f"日本語化に必要なコピー元ファイルなし: {path}")
                return False
        return True

    def _check_or_create_destination_dirs(self, locale_dir: Path, fonts_dir: Path) -> bool:
        """コピー先のディレクトリが存在するか確認、なければ作成試行。"""
        if not locale_dir.is_dir():
            self._status_string_for_ui = f"エラー: ゲームのLocaleフォルダが見つかりません。\nパス: {locale_dir}\nゲームのインストール先を確認してください。"
            logger.error(f"コピー先Localeフォルダなし: {locale_dir}")
            return False
        if not fonts_dir.is_dir():
            logger.warning(f"ゲームのUI/Resource/Fontsフォルダが見つかりません: {fonts_dir}。作成を試みます。")
            try:
                fonts_dir.mkdir(parents=True, exist_ok=True)
            except OSError as e:
                self._status_string_for_ui = f"エラー: Fontsフォルダの作成に失敗しました。\nパス: {fonts_dir}\nエラー: {e}\n手動で作成してみてください。"
                logger.error(f"Fontsフォルダ作成失敗: {fonts_dir}, {e}", exc_info=True)
                return False
        return True

    def _copy_translation_files(self, source_files_map: Dict[str, Path], destination_paths_map: Dict[str, Path]) -> bool:
        """翻訳関連ファイルを実際にコピーする。"""
        try:
            for name_key, dest_path in destination_paths_map.items():
                source_path = source_files_map[name_key]  # キーが一致することを前提
                logger.info(f"ファイルコピー実行: {source_path} -> {dest_path}")
                shutil.copy2(str(source_path), str(dest_path))  # shutilは文字列パスを要求
            self._status_string_for_ui = "日本語化ファイルの適用が完了しました。\n「PLAY」ボタンを押してゲームを開始してください。"
            logger.info("日本語化ファイルのコピーが全て成功しました。")
            return True
        except (shutil.Error, IOError, OSError) as e:  # PermissionErrorなども含む
            self._status_string_for_ui = f"エラー: 日本語化ファイルのコピー中に問題が発生しました。\n詳細: {type(e).__name__} - {e}\nファイル権限やディスク空き容量を確認してください。"
            logger.error(f"日本語化ファイルのコピー失敗: {e}", exc_info=True)
        except Exception as e:  # 予期せぬエラー
            self._status_string_for_ui = f"エラー: 日本語化処理中に予期せぬ問題が発生しました。\n詳細: {type(e).__name__}"
            logger.error(f"日本語化処理中の予期せぬエラー: {e}", exc_info=True)
        return False

    def execute_translation_apply(self) -> None:
        """日本語化ファイルの適用処理を実行。"""
        logger.info("日本語化適用処理実行")
        self._status_string_for_ui = "日本語化ファイルの適用を開始します..."
        self._ui_manager.redraw_main_window_if_needed()

        local_game_path_str = self._config_manager.get_config_value(CONST.CONFIG_KEY_LOCAL_PATH)
        if not local_game_path_str:
            self._status_string_for_ui = "エラー: ゲームのインストールフォルダが設定されていません。設定画面から指定してください。"
            logger.error("ローカルパス未設定のため日本語化不可。")
            self._ui_manager.redraw_main_window_if_needed()
            return

        local_game_path = Path(local_game_path_str)
        data_source_dir = self._data_dir

        # コピー元ファイル定義
        source_files = {
            "翻訳DAT": data_source_dir / CONST.JP_DAT_FILE_NAME,
            "翻訳DIR": data_source_dir / CONST.JP_DIR_FILE_NAME,
            f"フォント({CONST.FONT_GEO_MD})": data_source_dir / CONST.FONT_DIR_NAME / CONST.FONT_GEO_MD,
            f"フォント({CONST.FONT_PS2_GEO_MD_ROSA_VERDE})": data_source_dir / CONST.FONT_DIR_NAME / CONST.FONT_PS2_GEO_MD_ROSA_VERDE,
        }
        if not self._check_source_files_exist(source_files):
            self._ui_manager.redraw_main_window_if_needed()
            return

        # コピー先ディレクトリとファイルパス定義
        locale_dir_dest = local_game_path / "Locale"
        fonts_dir_dest = local_game_path / "UI" / "Resource" / "Fonts"  # 元のコードから変更なし
        if not self._check_or_create_destination_dirs(locale_dir_dest, fonts_dir_dest):
            self._ui_manager.redraw_main_window_if_needed()
            return

        destination_paths = {
            "翻訳DAT": locale_dir_dest / CONST.EN_DAT_FILE_NAME,  # 上書き対象
            "翻訳DIR": locale_dir_dest / CONST.EN_DIR_FILE_NAME,  # 上書き対象
            f"フォント({CONST.FONT_GEO_MD})": fonts_dir_dest / CONST.FONT_GEO_MD,
            f"フォント({CONST.FONT_PS2_GEO_MD_ROSA_VERDE})": fonts_dir_dest / CONST.FONT_PS2_GEO_MD_ROSA_VERDE,
        }

        # 念のため、コピー対象の英語ファイルが存在するか確認 (なくても処理は続行)
        if not destination_paths["翻訳DAT"].exists() or not destination_paths["翻訳DIR"].exists():
            logger.warning(f"コピー対象の英語データファイル ({CONST.EN_DAT_FILE_NAME} または {CONST.EN_DIR_FILE_NAME}) がLocaleフォルダ内に見つかりません。処理は続行します。")

        self._copy_translation_files(source_files, destination_paths)  # 実際のコピー処理
        self._ui_manager.redraw_main_window_if_needed()

    def _check_single_entity_update(self, version_info: VersionInfo, entity_name_jp: str, is_developer_mode: bool) -> str:
        """単一エンティティ（アプリまたは翻訳）のアップデートを確認するヘルパー。"""
        status_message = ""
        if not self._github_resource_manager.check_repository_connection(version_info.server_url):
            status_message = f"{entity_name_jp}の更新サーバーに接続できません。"
            logger.warning(f"{entity_name_jp}更新サーバー ({version_info.server_url}) 接続不可。")
            version_info.latest_available = version_info.current
            return status_message

        latest_tag: Optional[str] = None
        if not is_developer_mode:
            # 通常モード: GitHubの「Latest release」ラベルが付いたタグを取得
            latest_tag = self._github_scraper.get_latest_release_tag(version_info.server_url)
            logger.info(f"通常モード: {entity_name_jp}の最新リリースタグ取得: {latest_tag}")
        else:
            # 開発者モード: 全てのリリースから最も高いバージョンを持つタグを取得
            all_releases = self._github_scraper.get_all_releases_info(version_info.server_url)
            if all_releases:
                # 開発者モードではプレリリース版も考慮して最も高いバージョンを選択
                latest_tag = self._github_scraper._get_highest_version_tag(all_releases, include_prerelease=True)
                logger.info(f"開発者モード: {entity_name_jp}の最高バージョンタグ取得: {latest_tag}")
            else:
                logger.warning(f"開発者モード: {entity_name_jp}の全リリース情報取得失敗 (サーバー: {version_info.server_url})")

        if latest_tag:
            try:
                version.parse(latest_tag)
                version_info.latest_available = latest_tag
                if version_info.is_update_available:  # VersionInfo.is_update_availableのロジックは変更される
                    status_message = f"新しい{entity_name_jp}のバージョン ({latest_tag}) が利用可能です。"
                else:
                    status_message = f"{entity_name_jp}は最新バージョンです。"
                logger.info(f"{entity_name_jp}の最新リリースタグ設定: {latest_tag}")
            except version.InvalidVersion:
                status_message = f"{entity_name_jp}の更新サーバーから無効なバージョン形式のタグ ({latest_tag}) を取得しました。"
                logger.warning(f"{entity_name_jp}の最新タグが無効なバージョン形式: {latest_tag} (サーバー: {version_info.server_url})")
                version_info.latest_available = version_info.current
        else:
            status_message = f"{entity_name_jp}の最新バージョン情報を取得できませんでした。"
            logger.warning(f"{entity_name_jp}の最新リリースタグ取得失敗 (サーバー: {version_info.server_url})")
            version_info.latest_available = version_info.current
        return status_message

    def execute_check_for_updates(self) -> None:
        """アプリケーション本体と翻訳データのアップデートを確認。"""
        logger.info("アップデート確認処理実行")
        self._status_string_for_ui = "アップデート情報を確認しています..."
        self._ui_manager.redraw_main_window_if_needed()

        # 設定から最新のサーバーURLをVersionInfoオブジェクトに反映
        self._app_version_info.server_url = self._config_manager.get_config_value(CONST.CONFIG_KEY_APP_UPDATE_SERVER_URL, CONST.DEFAULT_APP_UPDATE_SERVER_URL)
        self._translation_version_info.server_url = self._config_manager.get_config_value(CONST.CONFIG_KEY_TRANSLATION_UPDATE_SERVER_URL, CONST.DEFAULT_TRANSLATION_UPDATE_SERVER_URL)

        app_status = self._check_single_entity_update(self._app_version_info, "アプリケーション", self._config_manager.get_config_value(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE))
        trans_status = self._check_single_entity_update(self._translation_version_info, "翻訳データ", self._config_manager.get_config_value(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE))

        # ステータスメッセージを結合。両方空なら汎用メッセージ
        final_status_messages = [msg for msg in [app_status, trans_status] if msg]
        self._status_string_for_ui = "\n".join(final_status_messages) if final_status_messages else "アップデート情報の取得/確認が完了しました。"

        self._ui_manager.redraw_main_window_if_needed()  # 全てのバージョン情報をUIに反映
        logger.info(f"アップデート確認処理完了。ステータス: {self._status_string_for_ui.replace('\n', ' / ')}")

    def _internal_download_asset_wrapper(self, repo_url: str, tag: str, filename: str, dest_dir: str, progress_cb: Callable) -> str:
        """GitHubResourceManager.download_release_asset のラッパー。DownloadWorkerから呼ばれる。"""
        # このラッパーは、引数の型や順序をDownloadWorkerの期待に合わせるために存在
        return self._github_resource_manager.download_release_asset(repo_url_or_path=repo_url, tag_name=tag, asset_filename=filename, destination_directory=dest_dir, progress_callback=progress_cb)

    def _start_update_download(self, version_info_obj: VersionInfo, filenames_to_download: List[str], entity_name_japanese: str, on_download_finished_callback: Callable[[], None]) -> None:
        """指定エンティティのアップデートファイルダウンロードを開始する共通ロジック。"""
        logger.info(f"{entity_name_japanese}の更新ダウンロード処理開始")
        if not version_info_obj.is_update_available:
            self._status_string_for_ui = f"{entity_name_japanese}は既に最新バージョンです。ダウンロードは不要です。"
            logger.info(f"{entity_name_japanese}は最新のためダウンロードスキップ。")
            self._ui_manager.redraw_main_window_if_needed()
            return

        server_url = version_info_obj.server_url
        latest_tag_name = version_info_obj.latest_available
        if not server_url or not latest_tag_name:
            self._status_string_for_ui = f"エラー: {entity_name_japanese}の更新サーバーURLまたは最新タグが不明なため、ダウンロードできません。"
            logger.error(f"{entity_name_japanese}の更新サーバーURLまたは最新タグ不明のためダウンロード不可。")
            self._ui_manager.redraw_main_window_if_needed()
            return

        self._ui_manager.start_background_download(
            download_function=self._internal_download_asset_wrapper,
            repo_url=server_url,
            tag_name=latest_tag_name,
            filenames=filenames_to_download,
            destination_dir=self._data_dir,  # dataフォルダにダウンロード
            on_finished_callback=on_download_finished_callback,
        )

    def execute_app_update_download(self) -> None:
        """アプリケーションのアップデートファイルのダウンロードを開始。"""

        def on_app_download_completed():
            logger.info("アプリケーションファイルのダウンロード完了後の処理を開始します。")
            new_app_version = self._app_version_info.latest_available
            if not new_app_version:  # 通常ありえないが念のため
                logger.error("アプリの最新バージョン情報が失われたため、更新処理を中断します。")
                return

            self._status_string_for_ui = f"アプリ(Ver {new_app_version})のダウンロードが完了しました。\n updater.bat を実行して更新を適用します..."
            self._ui_manager.redraw_main_window_if_needed()  # UIにメッセージ表示

            self._config_manager.set_config_value(CONST.CONFIG_KEY_APP_VERSION, new_app_version)
            self._app_version_info.current = new_app_version  # 内部状態も更新

            time.sleep(0.2)  # updater.bat実行前の短いウェイト

            # updater.bat は data ディレクトリにある想定 (build.batでコピーされる)
            # プロジェクトルートは data ディレクトリの親
            project_root_dir = self._data_dir.parent
            updater_bat_path = self._data_dir / "updater.bat"  # dataフォルダ内のupdater.bat

            if updater_bat_path.is_file():
                logger.info(f"updater.bat を実行します: {updater_bat_path} (作業ディレクトリ: {project_root_dir})")
                try:
                    # os.startfileで別プロセスとして起動し、本体は終了する
                    os.startfile(str(updater_bat_path), cwd=str(project_root_dir))
                    logger.info("updater.bat の起動を試みました。アプリケーションを終了します。")
                    QApplication.instance().quit()  # アップデーターに処理を委ねて本体は終了
                except Exception as e:
                    err_msg = f"エラー: updater.bat の実行に失敗しました。\n詳細: {type(e).__name__} - {e}\n手動で {updater_bat_path.name} を実行してください。"
                    self._status_string_for_ui = err_msg
                    logger.error(f"{err_msg}", exc_info=True)
                    self._ui_manager.redraw_main_window_if_needed()  # エラーをUIに表示
            else:
                err_msg = f"エラー: 更新用バッチファイル (updater.bat) が見つかりません。\nパス: {updater_bat_path}\nツールを再ダウンロードしてください。"
                self._status_string_for_ui = err_msg
                logger.error(err_msg)
                self._ui_manager.redraw_main_window_if_needed()
            # updater.batが失敗した場合や見つからない場合は、再度バージョンチェックしてUIを最新化
            self.execute_check_for_updates()

        self._start_update_download(self._app_version_info, CONST.APP_UPDATE_FILENAMES, "アプリケーション", on_app_download_completed)

    def execute_translation_update_download(self) -> None:
        """翻訳データのアップデートファイルのダウンロードを開始。"""

        def on_translation_download_completed():
            logger.info("翻訳ファイルのダウンロード完了後の処理を開始します。")
            new_trans_version = self._translation_version_info.latest_available
            if not new_trans_version:  # 通常ありえない
                logger.error("翻訳データの最新バージョン情報が失われたため、更新処理を中断します。")
                return

            self._status_string_for_ui = f"翻訳データ(Ver {new_trans_version})のダウンロードが完了しました。必要に応じて「日本語化」ボタンで適用してください。"
            self._config_manager.set_config_value(CONST.CONFIG_KEY_TRANSLATION_VERSION, new_trans_version)
            self._translation_version_info.current = new_trans_version  # 内部状態も更新
            self.execute_check_for_updates()  # バージョン再チェックとUI更新

        # TODO: フォントファイルも翻訳アップデートに含めるかの検討 (現状はdat/dirのみ)
        # もしフォントも対象にする場合、CONST.TRANSLATION_UPDATE_FILENAMES に追加し、
        # 保存先ディレクトリを DownloadWorker側でファイル種別に応じて変更する工夫が必要になる。
        # (例: .ttf なら data/fonts/、それ以外は data/ 直下など)
        # ここでは、現状の仕様通り .dat と .dir のみをダウンロードする。
        self._start_update_download(self._translation_version_info, CONST.TRANSLATION_UPDATE_FILENAMES, "翻訳データ", on_translation_download_completed)

    def handle_developer_mode_changed_on_settings_close(self) -> None:
        """
        設定ポップアップが閉じられた際に、開発者モードの変更を検出し、
        必要に応じてバージョン情報の整合性チェックを再実行します。
        """
        logger.info("開発者モード変更に伴うバージョン整合性チェック開始。")
        current_developer_mode = self._config_manager.get_config_value(CONST.CONFIG_KEY_DEVELOPER_MODE, CONST.DEFAULT_DEVELOPER_MODE)

        if current_developer_mode != self._previous_developer_mode_state:
            logger.info(f"開発者モードが変更されました (旧: {self._previous_developer_mode_state}, 新: {current_developer_mode})。バージョン情報を再確認します。")
            # 開発者モードの切り替えがあった場合、強制的にアップデートチェックを再実行
            self.execute_check_for_updates()
            # 変更後の状態を記録
            self._previous_developer_mode_state = current_developer_mode
        else:
            logger.info("開発者モードは変更されていません。バージョン整合性チェックは不要です。")


# ----------------------------------------------------------------------
# 8. アプリケーションエントリーポイント
# ----------------------------------------------------------------------
def _setup_module_search_paths() -> None:
    """Pythonモジュールの検索パスを設定。単一ファイルでは効果は限定的だが念のため。"""
    logger.debug("モジュール検索パス設定（単一ファイルのため限定的）")
    # current_dir = Path(sys.executable if IS_FROZEN_APP else __file__).resolve().parent
    # if str(current_dir) not in sys.path:
    #     sys.path.append(str(current_dir))
    #     logger.debug(f"sys.path に追加: {current_dir}")


def _initialize_base_and_data_directories() -> Tuple[Path, Path]:
    """アプリケーションのベースディレクトリとデータディレクトリを決定し、環境変数に設定。"""
    logger.debug("ベースディレクトリとデータディレクトリの初期化開始")

    # 通常のPython環境
    # __file__ はスクリプトファイルの絶対パス
    base_dir = Path(__file__).resolve().parent
    data_dir = base_dir / "data"  # srcディレクトリの親がプロジェクトルート
    env_type_msg = "通常Python"

    if IS_FROZEN_APP:
        # Nuitkaでコンパイルされた場合
        # sys.argv[0] は実行ファイルの絶対パス (バージョン2.4以降) or 実行ファイル名
        exe_path = Path(sys.argv[0])
        if not exe_path.is_absolute():
            exe_path = Path(os.path.abspath(sys.argv[0]))
        base_dir = exe_path.parent
        data_dir = base_dir / "data"  # onefileの場合はdataを同階層に置くことを想定
        env_type_msg = "Nuitkaコンパイル(凍結)"

    logger.info(f"{env_type_msg}環境検出。ベースディレクトリ: {base_dir}, データディレクトリ: {data_dir}")

    os.environ["BASE_DIR"] = str(base_dir)
    os.environ["DATA_DIR"] = str(data_dir)
    logger.info(f"環境変数設定: BASE_DIR={base_dir}, DATA_DIR={data_dir}")

    try:  # data/fonts ディレクトリも確実に作成
        (data_dir / CONST.FONT_DIR_NAME).mkdir(parents=True, exist_ok=True)
        logger.debug(f"データディレクトリ (および {CONST.FONT_DIR_NAME} サブディレクトリ) の存在確認/作成完了: {data_dir}")
    except OSError as e:
        logger.error(f"データディレクトリまたは{CONST.FONT_DIR_NAME}サブディレクトリの作成に失敗しました: {data_dir}, エラー: {e}", exc_info=True)
        # ここでアプリケーションを終了させるか、エラーを通知することも検討
    return base_dir, data_dir


def _setup_file_logging(log_data_directory: Path) -> None:
    """ファイルへのログ出力を設定。DATA_DIR 確定後に呼び出す。"""
    global file_handler, formatter  # グローバルスコープの file_handler と formatter を参照
    log_file_path = log_data_directory / "ps2jpmod_app.log"
    try:
        # mode="a"で追記。ローテーション機能はないが、簡易的なログには十分。
        file_handler = logging.FileHandler(log_file_path, encoding="utf-8", mode="a")
        file_handler.setLevel(logging.DEBUG)  # ファイルにはDEBUGレベル以上を全て記録
        file_handler.setFormatter(formatter)  # コンソールと同じフォーマッタを使用
        logger.addHandler(file_handler)
        logger.info(f"ログファイル出力設定完了: {log_file_path}")
    except (IOError, OSError, Exception) as e:  # PermissionErrorなども含む
        logger.error(f"ログファイルハンドラの設定に失敗しました: {log_file_path}, エラー: {e}", exc_info=True)
        # ファイルログがなくてもコンソールログは機能するので、アプリ実行は継続


if __name__ == "__main__":
    # 引数が指定されている場合にバージョンを吐き出す機能
    if len(sys.argv) > 1:
        output_dir = sys.argv[1]  # 最初の引数を output フォルダのパスとする
        os.makedirs(output_dir, exist_ok=True)
        with open(os.path.join(output_dir, "version.txt"), "w") as f:
            f.write(CONST.DEFAULT_APP_VERSION)
        sys.exit(0)

    _setup_module_search_paths()
    app_base_dir, app_data_dir = _initialize_base_and_data_directories()
    if not IS_FROZEN_APP:
        _setup_file_logging(app_data_dir)  # DATA_DIR が確定してからファイルログ設定

    env_type_str = "Nuitkaコンパイル(凍結)" if IS_FROZEN_APP else "通常Python"
    log_header = f"PlanetSide 2 日本語化MOD 起動 (Ver: {CONST.DEFAULT_APP_VERSION}) - {env_type_str}"
    separator_line = "=" * (len(log_header) + 4)
    logger.info(separator_line)
    logger.info(f"  {log_header}  ")
    logger.info(separator_line)
    logger.info(f"ベースディレクトリ: {app_base_dir}")
    logger.info(f"データディレクトリ: {app_data_dir}")

    main_execution_success = False
    try:
        main_manager = MainManager(data_dir_path=str(app_data_dir))
        main_manager.initialize_application_state_and_ui()

        exit_code = main_manager._ui_manager.run_app_event_loop()
        logger.info(f"アプリケーションイベントループ終了。終了コード: {exit_code}")
        main_execution_success = True  # ここまで来れば正常終了
        sys.exit(exit_code)

    except Exception as e:  # 予期せぬ最上位レベルの例外
        logger.critical(f"アプリケーションの起動または実行中に致命的なエラーが発生しました: {e}", exc_info=True)
        # GUIが表示できる状態ならエラーダイアログを出すことも検討
        # GUI初期化前のエラーも考慮し、標準エラー出力にもメッセージ
        error_message_for_user = "アプリケーションの起動中に致命的なエラーが発生しました。\n" "詳細はログファイルを確認してください。\n\n" f"エラータイプ: {type(e).__name__}\n" f"エラー詳細: {e}"
        print(error_message_for_user, file=sys.stderr)
        if file_handler and hasattr(file_handler, "baseFilename"):  # ログファイルパスが分かれば表示
            print(f"ログファイル: {file_handler.baseFilename}", file=sys.stderr)

        # 簡単なGUIエラーメッセージボックスを表示しようと試みる (QApplicationインスタンスが存在する場合)
        if QApplication.instance():
            from PySide6.QtWidgets import QMessageBox  # ここでimport

            try:
                msg_box = QMessageBox()
                msg_box.setIcon(QMessageBox.Icon.Critical)
                msg_box.setWindowTitle("致命的なエラー")
                msg_box.setText("アプリケーションの実行中に予期せぬエラーが発生しました。")
                msg_box.setInformativeText(f"詳細: {type(e).__name__} - {e}\nログファイルを確認してください。")
                if file_handler and hasattr(file_handler, "baseFilename"):
                    msg_box.setDetailedText(f"ログファイル: {file_handler.baseFilename}\n\n{logging.Formatter().formatException(sys.exc_info())}")
                else:
                    msg_box.setDetailedText(f"{logging.Formatter().formatException(sys.exc_info())}")
                msg_box.setStandardButtons(QMessageBox.StandardButton.Ok)
                msg_box.exec()
            except Exception as e_msgbox:
                logger.error(f"エラーメッセージボックスの表示に失敗しました: {e_msgbox}", exc_info=True)

        sys.exit(1)  # エラー終了
    finally:
        if main_execution_success:
            logger.info("アプリケーションは正常に終了しました。")
        else:
            logger.warning("アプリケーションはエラーにより途中で終了した可能性があります。")
        logging.shutdown()  # ロギングシステムをシャットダウン
