from flask import Flask, render_template, request, redirect, url_for, session, flash, jsonify
import sqlite3
import requests
from config import Config
from datetime import datetime

app = Flask(__name__)
app.config.from_object(Config)


# ============ ФИЛЬТРЫ JINJA2 ============
@app.template_filter('team_name')
def team_name_filter(team_id, teams):
    """Получить имя команды по ID"""
    if not team_id:
        return 'TBD'
    for team in teams:
        if team['id'] == team_id:
            return team['name']
    return 'TBD'


# ============ РАБОТА С БАЗОЙ ДАННЫХ ============
def get_db():
    """Подключение к БД"""
    db = sqlite3.connect(Config.DATABASE)
    db.row_factory = sqlite3.Row
    return db


def init_db():
    """Инициализация базы данных"""
    db = get_db()
    cursor = db.cursor()

    # Таблица команд (обновлённая структура с двумя игроками)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS teams (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT UNIQUE NOT NULL,
            logo_emoji TEXT DEFAULT '🎮',
            player1_name TEXT,
            player1_steam TEXT,
            player2_name TEXT,
            player2_steam TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица участников (для обратной совместимости)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS players (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            nickname TEXT NOT NULL,
            steam_url TEXT NOT NULL,
            steam_id TEXT,
            team_id INTEGER NOT NULL,
            cs2_hours INTEGER DEFAULT 0,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            FOREIGN KEY (team_id) REFERENCES teams (id)
        )
    ''')

    # Таблица правил
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS rules (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            description TEXT NOT NULL,
            icon TEXT DEFAULT '📋',
            order_num INTEGER DEFAULT 0
        )
    ''')

    # Таблица сессий (кто вошёл)
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            player_name TEXT NOT NULL,
            team_name TEXT NOT NULL,
            login_time TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Таблица сессий veto карт
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS veto_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            team1_id INTEGER NOT NULL,
            team2_id INTEGER NOT NULL,
            banned_maps TEXT DEFAULT '',
            status TEXT DEFAULT 'active',
            final_map TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at TIMESTAMP,
            FOREIGN KEY (team1_id) REFERENCES teams (id),
            FOREIGN KEY (team2_id) REFERENCES teams (id)
        )
    ''')

    # Таблица матчей турнирной сетки
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            round_num INTEGER NOT NULL,
            match_num INTEGER NOT NULL,
            team1_id INTEGER,
            team2_id INTEGER,
            team1_score INTEGER DEFAULT 0,
            team2_score INTEGER DEFAULT 0,
            winner_id INTEGER,
            next_match_id INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    ''')

    # Создаём начальную сетку турнира если её нет
    cursor.execute('SELECT COUNT(*) FROM matches')
    if cursor.fetchone()[0] == 0:
        # 7 матчей: 4 четвертьфинала, 2 полуфинала, 1 финал
        matches = [
            (1, 1, None, None, 0, 0, None, 5, 'pending'),
            (1, 2, None, None, 0, 0, None, 5, 'pending'),
            (1, 3, None, None, 0, 0, None, 6, 'pending'),
            (1, 4, None, None, 0, 0, None, 6, 'pending'),
            (2, 1, None, None, 0, 0, None, 7, 'pending'),
            (2, 2, None, None, 0, 0, None, 7, 'pending'),
            (3, 1, None, None, 0, 0, None, None, 'pending'),
        ]
        cursor.executemany('''
            INSERT INTO matches (round_num, match_num, team1_id, team2_id, team1_score, team2_score, winner_id, next_match_id, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', matches)

    db.commit()
    db.close()


# ============ STEAM API ============
def get_steam_id_from_url(steam_url):
    """Извлекает Steam ID из URL профиля"""
    try:
        if steam_url.isdigit():
            return steam_url
        if '/profiles/' in steam_url:
            return steam_url.split('/profiles/')[1].strip('/')
        if '/id/' in steam_url:
            vanity = steam_url.split('/id/')[1].strip('/')
            url = f"https://api.steampowered.com/ISteamUser/ResolveVanityURL/v1/"
            response = requests.get(url, params={
                'key': Config.STEAM_API_KEY,
                'vanityurl': vanity
            }, timeout=10)
            data = response.json()
            if data.get('response', {}).get('success') == 1:
                return data['response']['steamid']
        return None
    except Exception as e:
        print(f"Ошибка получения Steam ID: {e}")
        return None


def get_cs2_hours(steam_id):
    """Получает часы в CS2 через Steam API"""
    if not steam_id or Config.STEAM_API_KEY == 'YOUR_STEAM_API_KEY_HERE':
        return 0
    try:
        url = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
        response = requests.get(url, params={
            'key': Config.STEAM_API_KEY,
            'steamid': steam_id,
            'format': 'json',
            'include_appinfo': 'true',
            'appids_filter[0]': Config.CS2_APP_ID
        }, timeout=10)
        data = response.json()
        games = data.get('response', {}).get('games', [])
        for game in games:
            if game.get('appid') == Config.CS2_APP_ID:
                return game.get('playtime_forever', 0) // 60
        return 0
    except Exception as e:
        print(f"Ошибка получения часов: {e}")
        return 0


# ============ МАРШРУТЫ ============
@app.before_request
def before_request():
    """Инициализация БД перед первым запросом"""
    init_db()


@app.route('/')
def index():
    """Главная страница"""
    db = get_db()
    teams_count = db.execute('SELECT COUNT(*) FROM teams').fetchone()[0]

    # Правильный подсчёт игроков
    teams_list = db.execute('SELECT * FROM teams').fetchall()
    players_count = 0
    for team in teams_list:
        if team['player1_name']:
            players_count += 1
        if team['player2_name']:
            players_count += 1

    total_matches = db.execute('SELECT COUNT(*) FROM matches WHERE status = "completed"').fetchone()[0]
    db.close()

    return render_template('index.html',
                           teams_count=teams_count,
                           players_count=players_count,
                           matches_count=total_matches,
                           user=session.get('user'))


@app.route('/register', methods=['GET', 'POST'])
def register():
    """Регистрация команды"""
    if request.method == 'POST':
        team_name = request.form.get('team_name', '').strip()
        player1_name = request.form.get('player1_name', '').strip()
        player1_steam = request.form.get('player1_steam', '').strip()
        player2_name = request.form.get('player2_name', '').strip()
        player2_steam = request.form.get('player2_steam', '').strip()

        if not all([team_name, player1_name, player1_steam, player2_name, player2_steam]):
            flash('Заполните все поля!', 'error')
        else:
            db = get_db()
            try:
                db.execute('''
                    INSERT INTO teams (name, player1_name, player1_steam, player2_name, player2_steam)
                    VALUES (?, ?, ?, ?, ?)
                ''', (team_name, player1_name, player1_steam, player2_name, player2_steam))
                db.commit()
                flash(f'Команда "{team_name}" успешно зарегистрирована!', 'success')
                db.close()
                return redirect(url_for('index'))
            except sqlite3.IntegrityError:
                flash('Команда с таким именем уже существует!', 'error')
            db.close()

    return render_template('register.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    """Страница входа"""
    db = get_db()
    teams = db.execute('SELECT * FROM teams ORDER BY name').fetchall()

    if request.method == 'POST':
        nickname = request.form.get('nickname', '').strip()
        team_id = request.form.get('team_id')

        if not nickname:
            flash('Введите ваш никнейм!', 'error')
        elif not team_id:
            flash('Выберите команду!', 'error')
        else:
            team = db.execute('SELECT * FROM teams WHERE id = ?', (team_id,)).fetchone()
            if team:
                session['user'] = {
                    'nickname': nickname,
                    'team': team['name'],
                    'team_id': team['id']
                }
                db.execute(
                    'INSERT INTO sessions (player_name, team_name) VALUES (?, ?)',
                    (nickname, team['name'])
                )
                db.commit()
                flash(f'Добро пожаловать, {nickname}!', 'success')
                db.close()
                return redirect(url_for('index'))

    db.close()
    return render_template('login.html', teams=teams)


@app.route('/logout')
def logout():
    """Выход"""
    session.pop('user', None)
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('index'))


@app.route('/maps')
def maps():
    """Выбор карт CS2"""
    maps_data = [
        {'name': 'Mirage', 'emoji': '🏜️', 'color': '#f4a261'},
        {'name': 'Dust II', 'emoji': '🏜️', 'color': '#e76f51'},
        {'name': 'Anubis', 'emoji': '🐫', 'color': '#e9c46a'},
        {'name': 'Ancient', 'emoji': '🏛️', 'color': '#2a9d8f'},
        {'name': 'Cache', 'emoji': '📦', 'color': '#264653'},
        {'name': 'Inferno', 'emoji': '🔥', 'color': '#e63946'},
        {'name': 'Nuke', 'emoji': '☢️', 'color': '#457b9d'},
        {'name': 'Vertigo', 'emoji': '🏙️', 'color': '#6a4c93'}
    ]
    return render_template('maps.html', maps=maps_data, user=session.get('user'))


@app.route('/rules')
def rules():
    """Правила"""
    db = get_db()
    rules_list = db.execute('SELECT * FROM rules ORDER BY order_num').fetchall()
    db.close()
    return render_template('rules.html', rules=rules_list, user=session.get('user'))


@app.route('/teams')
def teams():
    """Список команд"""
    db = get_db()
    teams_list = db.execute('SELECT * FROM teams ORDER BY name').fetchall()
    db.close()
    return render_template('teams.html', teams=teams_list, user=session.get('user'))


@app.route('/players')
def players():
    """Список участников"""
    db = get_db()
    teams_list = db.execute('SELECT * FROM teams ORDER BY name').fetchall()

    all_players = []
    for team in teams_list:
        # Игрок 1
        if team['player1_name']:
            steam_id = get_steam_id_from_url(team['player1_steam'])
            cs2_hours = get_cs2_hours(steam_id) if steam_id else 0
            print(f"Player 1: {team['player1_name']}, Steam ID: {steam_id}, CS2 Hours: {cs2_hours}")

            all_players.append({
                'nickname': team['player1_name'],
                'steam_url': team['player1_steam'],
                'team_name': team['name'],
                'logo_emoji': team['logo_emoji'] or '🎮',
                'cs2_hours': cs2_hours
            })
        # Игрок 2
        if team['player2_name']:
            steam_id = get_steam_id_from_url(team['player2_steam'])
            cs2_hours = get_cs2_hours(steam_id) if steam_id else 0
            print(f"Player 2: {team['player2_name']}, Steam ID: {steam_id}, CS2 Hours: {cs2_hours}")

            all_players.append({
                'nickname': team['player2_name'],
                'steam_url': team['player2_steam'],
                'team_name': team['name'],
                'logo_emoji': team['logo_emoji'] or '🎮',
                'cs2_hours': cs2_hours
            })

    db.close()
    return render_template('players.html', players=all_players, user=session.get('user'))


@app.route('/bracket')
def bracket():
    """Турнирная сетка"""
    db = get_db()
    quarterfinals = db.execute('SELECT * FROM matches WHERE round_num = 1 ORDER BY match_num').fetchall()
    semifinals = db.execute('SELECT * FROM matches WHERE round_num = 2 ORDER BY match_num').fetchall()
    final = db.execute('SELECT * FROM matches WHERE round_num = 3').fetchone()
    teams_list = db.execute('SELECT * FROM teams ORDER BY name').fetchall()
    db.close()

    return render_template('bracket.html',
                           quarterfinals=quarterfinals,
                           semifinals=semifinals,
                           final=final,
                           teams=teams_list,
                           user=session.get('user'))


@app.route('/api/bracket/update', methods=['POST'])
def update_match():
    """Обновить счёт матча"""
    data = request.json
    match_id = data.get('match_id')
    team1_score = data.get('team1_score', 0)
    team2_score = data.get('team2_score', 0)
    password = data.get('password')

    if password != Config.ADMIN_PASSWORD:
        return jsonify({'success': False, 'error': 'Неверный пароль'}), 403

    db = get_db()
    match = db.execute('SELECT * FROM matches WHERE id = ?', (match_id,)).fetchone()

    if not match:
        db.close()
        return jsonify({'success': False, 'error': 'Матч не найден'}), 404

    if team1_score > team2_score:
        winner_id = match['team1_id']
    elif team2_score > team1_score:
        winner_id = match['team2_id']
    else:
        db.close()
        return jsonify({'success': False, 'error': 'Ничья невозможна'}), 400

    db.execute('''
        UPDATE matches SET team1_score = ?, team2_score = ?, winner_id = ?, status = 'completed'
        WHERE id = ?
    ''', (team1_score, team2_score, winner_id, match_id))

    if match['next_match_id']:
        if match['match_num'] % 2 == 1:
            db.execute('UPDATE matches SET team1_id = ? WHERE id = ?', (winner_id, match['next_match_id']))
        else:
            db.execute('UPDATE matches SET team2_id = ? WHERE id = ?', (winner_id, match['next_match_id']))

    db.commit()
    db.close()

    return jsonify({'success': True})


# ============ АДМИН-ПАНЕЛЬ ============
@app.route('/admin', methods=['GET', 'POST'])
def admin():
    """Админ-панель"""
    if request.method == 'POST':
        password = request.form.get('password')
        if password == Config.ADMIN_PASSWORD:
            session['admin'] = True
            flash('Вход выполнен!', 'success')
        else:
            flash('Неверный пароль!', 'error')

    if not session.get('admin'):
        return render_template('admin.html', logged_in=False)

    db = get_db()
    teams = db.execute('SELECT * FROM teams ORDER BY name').fetchall()
    rules_list = db.execute('SELECT * FROM rules ORDER BY order_num').fetchall()
    matches = db.execute('SELECT * FROM matches ORDER BY round_num, match_num').fetchall()
    db.close()

    return render_template('admin.html',
                           logged_in=True,
                           teams=teams,
                           rules=rules_list,
                           matches=matches)


@app.route('/admin/add_rule', methods=['POST'])
def add_rule():
    """Добавить правило"""
    if not session.get('admin'):
        return redirect(url_for('admin'))

    title = request.form.get('title', '').strip()
    description = request.form.get('description', '').strip()
    icon = request.form.get('icon', '📋').strip() or '📋'
    order_num = request.form.get('order_num', 0)

    if title and description:
        db = get_db()
        db.execute(
            'INSERT INTO rules (title, description, icon, order_num) VALUES (?, ?, ?, ?)',
            (title, description, icon, order_num)
        )
        db.commit()
        db.close()
        flash('Правило добавлено!', 'success')

    return redirect(url_for('admin'))


@app.route('/admin/delete/<type>/<int:item_id>')
def delete_item(type, item_id):
    """Удаление элемента"""
    if not session.get('admin'):
        return redirect(url_for('admin'))

    db = get_db()

    if type == 'team':
        # Получаем информацию о команде перед удалением
        team = db.execute('SELECT * FROM teams WHERE id = ?', (item_id,)).fetchone()

        if team:
            team_name = team['name']

            # Очищаем связи в матчах (сбрасываем team1_id и team2_id)
            db.execute('UPDATE matches SET team1_id = NULL, team1_score = 0 WHERE team1_id = ?', (item_id,))
            db.execute('UPDATE matches SET team2_id = NULL, team2_score = 0 WHERE team2_id = ?', (item_id,))

            # Очищаем связи в veto-сессиях
            db.execute('UPDATE veto_sessions SET status = "cancelled" WHERE team1_id = ? OR team2_id = ?',
                      (item_id, item_id))

            # Удаляем записи о входах игроков этой команды
            db.execute('DELETE FROM sessions WHERE team_name = ?', (team['name'],))

            # Удаляем саму команду
            db.execute('DELETE FROM teams WHERE id = ?', (item_id,))

            db.commit()
            flash(f'Команда "{team_name}" и все связанные данные удалены!', 'success')
        else:
            flash('Команда не найдена!', 'error')

    elif type == 'rule':
        db.execute('DELETE FROM rules WHERE id = ?', (item_id,))
        db.commit()
        flash('Правило удалено!', 'success')

    db.close()
    return redirect(url_for('admin'))


@app.route('/admin/logout')
def admin_logout():
    """Выход из админки"""
    session.pop('admin', None)
    return redirect(url_for('admin'))


# ============ СИСТЕМА VETO КАРТ ============
@app.route('/veto')
def veto():
    """Страница veto карт"""
    db = get_db()
    veto_session = db.execute(
        'SELECT * FROM veto_sessions WHERE status = "active" ORDER BY created_at DESC LIMIT 1'
    ).fetchone()

    if veto_session:
        team1 = db.execute('SELECT * FROM teams WHERE id = ?', (veto_session['team1_id'],)).fetchone()
        team2 = db.execute('SELECT * FROM teams WHERE id = ?', (veto_session['team2_id'],)).fetchone()
        remaining_maps = [m for m in get_all_maps() if m['name'] not in (veto_session['banned_maps'] or '').split(',')]
        banned_count = len([m for m in (veto_session['banned_maps'] or '').split(',') if m])
        current_team = team1 if banned_count % 2 == 0 else team2
        db.close()
        return render_template('veto.html',
                               veto_session=veto_session,
                               team1=team1,
                               team2=team2,
                               remaining_maps=remaining_maps,
                               current_team=current_team,
                               banned_count=banned_count)

    db.close()
    return render_template('veto.html',
                           veto_session=None,
                           team1=None,
                           team2=None,
                           remaining_maps=get_all_maps(),
                           current_team=None,
                           banned_count=0)


@app.route('/api/veto/status')
def veto_status():
    """API для получения статуса veto"""
    db = get_db()
    veto_session = db.execute(
        'SELECT * FROM veto_sessions WHERE status = "active" ORDER BY created_at DESC LIMIT 1'
    ).fetchone()

    if not veto_session:
        db.close()
        return jsonify({'active': False})

    team1 = db.execute('SELECT * FROM teams WHERE id = ?', (veto_session['team1_id'],)).fetchone()
    team2 = db.execute('SELECT * FROM teams WHERE id = ?', (veto_session['team2_id'],)).fetchone()
    banned_maps = [m for m in (veto_session['banned_maps'] or '').split(',') if m]
    remaining_maps = [m for m in get_all_maps() if m['name'] not in banned_maps]
    banned_count = len(banned_maps)
    current_team_id = veto_session['team1_id'] if banned_count % 2 == 0 else veto_session['team2_id']
    current_team = team1 if current_team_id == team1['id'] else team2
    db.close()

    return jsonify({
        'active': True,
        'veto_id': veto_session['id'],
        'team1': dict(team1),
        'team2': dict(team2),
        'remaining_maps': remaining_maps,
        'banned_maps': banned_maps,
        'current_team': dict(current_team),
        'banned_count': banned_count,
        'final_map': remaining_maps[0] if len(remaining_maps) == 1 else None
    })


@app.route('/api/veto/ban', methods=['POST'])
def veto_ban():
    """Исключить карту"""
    data = request.json
    map_name = data.get('map')
    password = data.get('password')

    if password != Config.ADMIN_PASSWORD:
        return jsonify({'success': False, 'error': 'Неверный пароль'}), 403

    db = get_db()
    veto_session = db.execute(
        'SELECT * FROM veto_sessions WHERE status = "active" ORDER BY created_at DESC LIMIT 1'
    ).fetchone()

    if not veto_session:
        db.close()
        return jsonify({'success': False, 'error': 'Нет активной сессии'}), 404

    banned_maps = [m for m in (veto_session['banned_maps'] or '').split(',') if m]

    if map_name in banned_maps:
        db.close()
        return jsonify({'success': False, 'error': 'Карта уже исключена'}), 400

    banned_maps.append(map_name)
    remaining = [m for m in get_all_maps() if m['name'] not in banned_maps]

    if len(remaining) == 1:
        db.execute(
            'UPDATE veto_sessions SET banned_maps = ?, status = "completed", final_map = ?, completed_at = CURRENT_TIMESTAMP WHERE id = ?',
            (','.join(banned_maps), remaining[0]['name'], veto_session['id'])
        )
    else:
        db.execute(
            'UPDATE veto_sessions SET banned_maps = ? WHERE id = ?',
            (','.join(banned_maps), veto_session['id'])
        )

    db.commit()
    db.close()

    return jsonify({
        'success': True,
        'remaining': len(remaining),
        'final_map': remaining[0]['name'] if len(remaining) == 1 else None
    })


@app.route('/admin/start_veto', methods=['POST'])
def start_veto():
    """Запустить сессию veto"""
    if not session.get('admin'):
        return redirect(url_for('admin'))

    team1_id = request.form.get('team1_id')
    team2_id = request.form.get('team2_id')

    if team1_id and team2_id and team1_id != team2_id:
        db = get_db()
        db.execute('UPDATE veto_sessions SET status = "cancelled" WHERE status = "active"')
        db.execute(
            'INSERT INTO veto_sessions (team1_id, team2_id, status) VALUES (?, ?, "active")',
            (team1_id, team2_id)
        )
        db.commit()
        db.close()
        flash('Сессия veto запущена!', 'success')
    else:
        flash('Выберите две разные команды!', 'error')

    return redirect(url_for('admin'))


@app.route('/admin/cancel_veto')
def cancel_veto():
    """Отменить veto"""
    if not session.get('admin'):
        return redirect(url_for('admin'))

    db = get_db()
    db.execute('UPDATE veto_sessions SET status = "cancelled" WHERE status = "active"')
    db.commit()
    db.close()
    flash('Veto отменено', 'info')
    return redirect(url_for('admin'))


def get_all_maps():
    """Получить все карты"""
    return [
        {'name': 'Mirage', 'emoji': '🏜️', 'color': '#f4a261'},
        {'name': 'Dust II', 'emoji': '🏜️', 'color': '#e76f51'},
        {'name': 'Anubis', 'emoji': '🐫', 'color': '#e9c46a'},
        {'name': 'Ancient', 'emoji': '🏛️', 'color': '#2a9d8f'},
        {'name': 'Cache', 'emoji': '📦', 'color': '#264653'},
        {'name': 'Inferno', 'emoji': '🔥', 'color': '#e63946'},
        {'name': 'Nuke', 'emoji': '☢️', 'color': '#457b9d'},
        {'name': 'Vertigo', 'emoji': '🏙️', 'color': '#6a4c93'}
    ]


# ============ ЗАПУСК ============
if __name__ == '__main__':
    print("=" * 60)
    print("🎮 CS2 OverHeat Website запущен!")
    print("📍 Откройте в браузере: http://127.0.0.1:5000")
    print("🔐 Админ-панель: http://127.0.0.1:5000/admin")
    print("=" * 60)
    app.run(host='0.0.0.0', port=5000, debug=False)
