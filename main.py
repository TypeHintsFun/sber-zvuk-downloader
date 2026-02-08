import re
import httpx
import time
import random
from pathlib import Path

# ==============================================================================
# КОНФИГУРАЦИЯ ПОЛЬЗОВАТЕЛЯ
# ==============================================================================

# 1. Ваши куки из браузера (F12 -> Network -> Headers -> Cookie)
# Обязательно должны быть: auth, access_token, session_id, wafv, device_id
COOKIES_DICT = {}

# 2. Путь для сохранения (по умолчанию папка downloads в директории скрипта)
BASE_DOWNLOAD_PATH = Path("downloads")

# 3. Настройки "реализма" (паузы в секундах)
DELAY_TRACK_MIN = 1.0  # Минимальная пауза между песнями
DELAY_TRACK_MAX = 4.0  # Максимальная пауза между песнями
DELAY_EVERY_N_TRACKS = 10  # Каждые N треков делать большую паузу
DELAY_BIG_MIN = 10.0  # Минимальная большая пауза
DELAY_BIG_MAX = 20.0  # Максимальная большая пауза
DELAY_PLAYLIST_MIN = 30.0  # Пауза между плейлистами в режиме 'all'
DELAY_PLAYLIST_MAX = 60.0

# 4. Настройки идентификации браузера
USER_AGENT = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0'


class ZvukClient:
    def __init__(self, cookies: dict):
        self.client = httpx.Client(http2=True, timeout=30.0, follow_redirects=True)
        self.base_headers = {
            'Host': 'zvuk.com',
            'User-Agent': USER_AGENT,
            'Accept': 'application/json, text/plain, */*',
            'Referer': 'https://zvuk.com/',
            'Origin': 'https://zvuk.com',
        }
        self.client.headers.update(self.base_headers)
        self.client.cookies.update(cookies)
        self.auth_token = cookies.get('auth')

    def _graphql(self, op_name, query, vars=None):
        url = 'https://zvuk.com/api/v1/graphql'
        headers = {
            'content-type': 'application/json',
            'x-app-name': 'web-zvuk-service-desktop-app',
            'x-auth-token': self.auth_token,
            'Accept': 'application/graphql-response+json, application/json',
        }
        try:
            resp = self.client.post(url, headers=headers,
                                    json={'operationName': op_name, 'query': query, 'variables': vars or {}})
            return resp.json()
        except Exception as e:
            print(f"\n[!] Ошибка сети в GraphQL ({op_name}): {e}")
            return {}

    def get_profile(self):
        url = 'https://zvuk.com/api/tiny/profile'
        return self.client.get(url).json()

    def get_collection_playlists(self):
        query = "query getColIds { collection { playlists { id title } } }"
        data = self._graphql('getColIds', query)
        return data.get('data', {}).get('collection', {}).get('playlists', [])

    def get_playlist_tracks(self, playlist_id):
        all_tracks = []
        offset = 0
        limit = 100
        while True:
            query = """
            query getPlaylistTracks($id: ID!, $limit: Int, $offset: Int) {
              playlistTracks(id: $id, limit: $limit, offset: $offset) {
                id title artists { title }
              }
            }"""
            data = self._graphql('getPlaylistTracks', query, {'id': str(playlist_id), 'limit': limit, 'offset': offset})
            tracks = data.get('data', {}).get('playlistTracks', [])
            if not tracks: break
            all_tracks.extend(tracks)
            if len(tracks) < limit: break
            offset += limit
        return all_tracks

    def get_favorites_tracks(self):
        all_tracks = []
        after = ""
        while True:
            query = """
            query getPaginatedCollection($limit: Int, $after: String) {
              paginatedCollection {
                tracks(pagination: {first: $limit, after: $after}) {
                  items { id title artists { title } }
                  page { endCursor }
                }
              }
            }"""
            data = self._graphql('getPaginatedCollection', query, {'limit': 100, 'after': after})
            conn = data.get('data', {}).get('paginatedCollection', {}).get('tracks', {})
            items = conn.get('items', [])
            if not items: break
            all_tracks.extend(items)
            after = conn.get('page', {}).get('endCursor')
            if not after: break
        return all_tracks

    def get_stream_url(self, track_id):
        query = """
        query getStream($ids: [ID!]!, $quality: String, $encodeType: String, $includeFlacDrm: Boolean!, $useHLSv2: Boolean!) {
          mediaContents(ids: $ids, quality: $quality, encodeType: $encodeType) {
            ... on Track {
              stream { expire high mid preview flacdrm @include(if: $includeFlacDrm) }
              streamV3 @include(if: $useHLSv2) { expire hls }
            }
          }
        }"""
        vars = {'ids': [str(track_id)], 'quality': 'hq', 'encodeType': 'wv', 'includeFlacDrm': False, 'useHLSv2': False}
        return self._graphql('getStream', query, vars)


def clean_name(name):
    """Удаляет символы, запрещенные в названиях папок и файлов"""
    return re.sub(r'[\\/*?:"<>|]', "", str(name)).strip()


def download_track_list(client, tracks, folder_path, ref_url):
    folder_path.mkdir(parents=True, exist_ok=True)
    print(f"\n[*] Плейлист: {folder_path.name}")
    print(f"[*] Треков найдено: {len(tracks)}")

    for i, track in enumerate(tracks, 1):
        t_id = track['id']
        artists = ", ".join([a['title'] for a in track['artists']])
        filename = clean_name(f"{artists} - {track['title']}") + ".mp3"
        file_path = folder_path / filename

        if file_path.exists():
            continue

        print(f"    [{i}/{len(tracks)}] {filename}", end=" ", flush=True)

        # Имитация паузы между треками
        time.sleep(random.uniform(DELAY_TRACK_MIN, DELAY_TRACK_MAX))
        if i % DELAY_EVERY_N_TRACKS == 0:
            p = random.uniform(DELAY_BIG_MIN, DELAY_BIG_MAX)
            print(f"(отдых {round(p)}с)", end=" ", flush=True)
            time.sleep(p)

        client.client.headers.update({'Referer': ref_url})
        res = client.get_stream_url(t_id)
        media = res.get('data', {}).get('mediaContents', [])

        if not media or not media[0].get('stream'):
            print("-> [НЕТ ДОСТУПА]")
            continue

        s = media[0]['stream']
        url = s.get('high') or s.get('mid') or s.get('preview')
        if not url:
            print("-> [НЕТ ССЫЛКИ]")
            continue

        try:
            # Скачиваем через отдельный запрос, чтобы не передавать лишние заголовки на CDN
            with httpx.stream("GET", url, timeout=60.0) as resp:
                if resp.status_code == 200:
                    with open(file_path, "wb") as f:
                        for chunk in resp.iter_bytes(): f.write(chunk)
                    print("-> [OK]")
                else:
                    print(f"-> [ERR {resp.status_code}]")
        except Exception as e:
            print(f"-> [ОШИБКА: {e}]")


if __name__ == "__main__":
    print("=== Zvuk.com Downloader ===")

    if not COOKIES_DICT or "auth" not in COOKIES_DICT:
        print("[!] ОШИБКА: Заполните COOKIES_DICT в начале скрипта!")
        exit()

    z = ZvukClient(COOKIES_DICT)

    try:
        # Проверка профиля
        profile_res = z.get_profile()
        if 'result' not in profile_res:
            print("[!] ОШИБКА: Куки невалидны или протухли. Обновите их.");
            exit()

        user_name = profile_res['result'].get('name', 'User')
        print(f"[+] Авторизован как: {user_name}")

        # Получаем список плейлистов
        playlists = z.get_collection_playlists()

        print("\nДоступные действия:")
        print(" [all] Скачать ВСЁ (Избранное + все плейлисты)")
        print(" [0]   Скачать только 'Избранное' (лайки)")
        for p in playlists:
            print(f" [{p['id']}] {p['title']}")

        choice = input('\nВведите ID или all: ').strip().lower()

        # Очередь задач
        tasks = []

        if choice == 'all':
            tasks.append(('0', 'My Favorites', 'fav'))
            for p in playlists:
                tasks.append((p['id'], p['title'], 'pl'))
        elif choice == '0':
            tasks.append(('0', 'My Favorites', 'fav'))
        else:
            # Ищем название для папки по введенному ID
            title = next((p['title'] for p in playlists if str(p['id']) == choice), f"Unknown_{choice}")
            tasks.append((choice, title, 'pl'))

        # Выполнение задач
        for idx, (pl_id, pl_title, t_type) in enumerate(tasks):
            dir_name = clean_name(f"{pl_title} [{pl_id}]")
            save_dir = BASE_DOWNLOAD_PATH / dir_name

            if t_type == 'fav':
                tracks = z.get_favorites_tracks()
                ref = "https://zvuk.com/favorites"
            else:
                tracks = z.get_playlist_tracks(pl_id)
                ref = f"https://zvuk.com/playlist/{pl_id}"

            download_track_list(z, tracks, save_dir, ref)

            # Если это не последний плейлист в списке 'all', делаем большую паузу
            if len(tasks) > 1 and idx < len(tasks) - 1:
                wait_between = random.uniform(DELAY_PLAYLIST_MIN, DELAY_PLAYLIST_MAX)
                print(f"\n[!] Ожидание перед следующим плейлистом: {round(wait_between)} сек...")
                time.sleep(wait_between)

    except KeyboardInterrupt:
        print("\n[!] Работа прервана пользователем.")
    except Exception as e:
        print(f"\n[!] Критическая ошибка: {e}")

    print("\n[ЗАВЕРШЕНО] Все доступные задачи выполнены.")
