#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
透过西伯利亚大铁路看俄罗斯 - 课堂成果共享服务器
打包成 exe 后可直接运行，无需安装 Python
用法：server.exe  或  python server.py
"""

import os
import sys
import json
import base64
import time
import uuid
import socket
import shutil
import threading
import urllib.request
import urllib.parse
from pathlib import Path
from datetime import datetime

# ============ 路径处理（同时支持 exe 和 py 运行） ============
def get_base_dir():
    """获取程序根目录（exe 模式 vs py 模式）"""
    if getattr(sys, 'frozen', False):
        # PyInstaller 打包后的 exe 模式
        return Path(sys._MEIPASS).resolve()
    else:
        # 普通 python server.py 模式
        return Path(__file__).parent.resolve()

BASE_DIR = get_base_dir()
# static/ 从 exe 内部读取（只读）
STATIC_DIR = BASE_DIR / "static"
# results.json 和 uploads/ 写在 exe 同级目录（可写）
EXE_DIR = Path(sys.executable).parent.resolve() if getattr(sys, 'frozen', False) else BASE_DIR
UPLOADS_DIR = EXE_DIR / "uploads"
DATA_FILE = EXE_DIR / "results.json"
PORT = int(os.environ.get('PORT', 8888))
IS_CLOUD = os.environ.get('CLOUD_DEPLOY', '') == '1'

# ============ Flask 导入 ============
from flask import Flask, request, jsonify, send_from_directory, send_file
from flask_cors import CORS

# 创建必要目录
STATIC_DIR.mkdir(exist_ok=True)
UPLOADS_DIR.mkdir(exist_ok=True)

# ============ Flask 应用 ============
app = Flask(__name__, static_folder=str(STATIC_DIR), static_url_path='')
CORS(app, resources={r"/api/*": {"origins": "*"}})

# ============ 内存数据存储 ============
_results = {}  # { group_id: {...} }
_lock = threading.Lock()
_access_log = []  # 访问日志
_log_lock = threading.Lock()

def add_log(ip, method, path, status, ua=""):
    """记录访问日志"""
    with _log_lock:
        _access_log.append({
            "time": datetime.now().strftime('%H:%M:%S'),
            "ip": ip,
            "method": method,
            "path": path,
            "status": status,
            "ua": ua[:50] if ua else ""
        })
        # 只保留最近 100 条
        if len(_access_log) > 100:
            _access_log.pop(0)

def load_results():
    """从文件加载成果数据"""
    global _results
    if DATA_FILE.exists():
        try:
            with open(DATA_FILE, 'r', encoding='utf-8') as f:
                _results = json.load(f)
            print(f"[服务器] 已加载 {len(_results)} 条历史成果")
        except Exception as e:
            print(f"[服务器] 加载历史成果失败: {e}")
            _results = {}

def save_results():
    """保存成果数据到文件"""
    try:
        with open(DATA_FILE, 'w', encoding='utf-8') as f:
            json.dump(_results, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[服务器] 保存成果失败: {e}")

# ============ API 接口 ============

@app.before_request
def log_request():
    """记录所有请求"""
    # 只记录重要请求，跳过静态文件
    skip_paths = ['/uploads/', '/static/', '/favicon.ico']
    if not any(request.path.startswith(s) for s in skip_paths):
        ua = request.headers.get('User-Agent', '')
        # 在 after_request 里记录状态


@app.after_request
def after_log(response):
    """记录响应状态"""
    skip_paths = ['/uploads/', '/static/', '/favicon.ico']
    if not any(request.path.startswith(s) for s in skip_paths):
        ua = request.headers.get('User-Agent', '')
        add_log(
            ip=request.remote_addr,
            method=request.method,
            path=request.path,
            status=response.status_code,
            ua=ua
        )
    return response


@app.route('/api/logs')
def api_logs():
    """返回最近的访问日志（老师诊断用）"""
    with _log_lock:
        return jsonify({"logs": list(reversed(_access_log[-50:]))})


@app.route('/api/ping')
def api_ping():
    """连通性测试接口"""
    return jsonify({"status": "ok", "msg": "课堂服务器运行中"})

@app.route('/api/config')
def api_config():
    """返回服务器配置信息"""
    return jsonify({
        "server_mode": True,
        "deepseek_available": False,
        "version": "1.0",
        "local_ip": get_local_ip(),
        "all_ips": get_all_local_ips(),
        "port": request.host.split(':')[-1] if ':' in request.host else str(PORT),
        "host_url": request.host_url.rstrip('/')
    })

@app.route('/api/submit', methods=['POST'])
def api_submit():
    """接收学生提交的成果"""
    try:
        data = request.get_json(force=True)
        if not data:
            return jsonify({"status": "error", "error": "无数据"}), 400

        group_id = data.get('group_id', '')
        if not group_id:
            return jsonify({"status": "error", "error": "缺少 group_id"}), 400

        # 处理图片：如果是 base64 则保存为文件
        images = data.get('images', [])
        new_image_urls = []
        for i, img in enumerate(images):
            if img and img.startswith('data:image'):
                # base64 图片 -> 保存为文件
                url = save_base64_image(img, group_id, i)
                new_image_urls.append(url)
            elif img and (img.startswith('/uploads/') or img.startswith('http')):
                # 已经是URL
                new_image_urls.append(img)
            else:
                new_image_urls.append(img)

        data['images'] = new_image_urls
        data['submit_time'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        with _lock:
            _results[group_id] = data
            save_results()

        print(f"[服务器] 收到成果: {data.get('group_name','?')} 第{data.get('group_num','?')}组")

        return jsonify({
            "status": "ok",
            "image_urls": new_image_urls
        })

    except Exception as e:
        print(f"[服务器] 提交成果出错: {e}")
        import traceback; traceback.print_exc()
        return jsonify({"status": "error", "error": str(e)}), 500

def save_base64_image(data_url, group_id, index):
    """将 base64 图片保存为文件，返回访问URL"""
    try:
        # 解析 base64
        header, b64 = data_url.split(',', 1)
        ext = 'jpg'
        if 'png' in header:
            ext = 'png'
        elif 'gif' in header:
            ext = 'gif'
        elif 'webp' in header:
            ext = 'webp'

        img_bytes = base64.b64decode(b64)

        # 生成文件名（安全的ASCII文件名）
        safe_gid = group_id.replace('/', '_').replace('\\', '_')
        fname = f"{safe_gid}_{index}_{int(time.time()*1000)}.{ext}"
        fpath = UPLOADS_DIR / fname

        with open(fpath, 'wb') as f:
            f.write(img_bytes)

        return f"/uploads/{fname}"
    except Exception as e:
        print(f"[服务器] 保存图片失败: {e}")
        return ''

@app.route('/api/upload_image', methods=['POST'])
def api_upload_image():
    """接收上传的图片文件"""
    try:
        if 'image' not in request.files:
            return jsonify({"error": "没有图片文件"}), 400

        f = request.files['image']
        if not f.filename:
            return jsonify({"error": "文件名为空"}), 400

        # 安全的文件扩展名
        ext = 'jpg'
        fn_lower = f.filename.lower()
        if fn_lower.endswith('.png'):
            ext = 'png'
        elif fn_lower.endswith('.gif'):
            ext = 'gif'
        elif fn_lower.endswith('.webp'):
            ext = 'webp'

        fname = f"{uuid.uuid4().hex}.{ext}"
        fpath = UPLOADS_DIR / fname
        f.save(str(fpath))

        url = f"/uploads/{fname}"
        print(f"[服务器] 上传图片: {fname}")
        return jsonify({"url": url, "status": "ok"})

    except Exception as e:
        print(f"[服务器] 上传图片出错: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/uploads/<filename>')
def serve_upload(filename):
    """提供上传图片的访问"""
    return send_from_directory(str(UPLOADS_DIR), filename)

@app.route('/api/results')
def api_results():
    """返回所有小组的成果"""
    with _lock:
        return jsonify(dict(_results))

@app.route('/api/clear', methods=['POST'])
def api_clear():
    """清除所有成果（仅限本地使用）"""
    with _lock:
        _results.clear()
        save_results()
        # 清除上传的图片
        for f in UPLOADS_DIR.iterdir():
            try:
                f.unlink()
            except:
                pass
    print("[服务器] 已清除所有成果和图片")
    return jsonify({"status": "ok"})

@app.route('/api/clear_group', methods=['POST'])
def api_clear_group():
    """清除指定小组的成果"""
    try:
        data = request.get_json(force=True)
        group_id = data.get('group_id', '').strip()
        if not group_id:
            return jsonify({"error": "缺少 group_id"}), 400

        with _lock:
            if group_id in _results:
                del _results[group_id]
                save_results()
                print(f"[服务器] 已清除 {group_id} 的成果")
                return jsonify({"status": "ok"})
            else:
                return jsonify({"status": "not_found", "message": "该组没有提交记录"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

@app.route('/api/web_search', methods=['POST'])
def api_web_search():
    """联网搜索（维基百科 + DuckDuckGo，无需 API Key）"""
    try:
        data = request.get_json(force=True)
        query = data.get('query', '').strip()
        if not query:
            return jsonify({"answer": "请输入搜索内容"}), 400

        # 增强查询：自动补充课堂相关关键词，提高相关性
        enhanced = query + ' 西伯利亚 俄罗斯 铁路'

        # —— 第1步：维基百科 ——
        try:
            sq = urllib.parse.quote(enhanced)
            url = (f"https://zh.wikipedia.org/w/api.php"
                    f"?action=query&list=search&srsearch={sq}"
                    f"&srlimit=3&format=json&origin=*")
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=6) as resp:
                sd = json.loads(resp.read().decode('utf-8', errors='ignore'))

            results = sd.get('query', {}).get('search', [])
            if results:
                pid = results[0]['pageid']
                ex_url = (f"https://zh.wikipedia.org/w/api.php"
                           f"?action=query&pageids={pid}"
                           f"&prop=extracts&exintro=true"
                           f"&explaintext=true&format=json&origin=*")
                req2 = urllib.request.Request(
                    ex_url, headers={'User-Agent': 'Mozilla/5.0'})
                with urllib.request.urlopen(req2, timeout=6) as resp2:
                    ed = json.loads(resp2.read().decode('utf-8', errors='ignore'))

                pages = ed.get('query', {}).get('pages', {})
                page = list(pages.values())[0]
                extract = page.get('extract', '')
                title = page.get('title', query)
                if extract and len(extract) > 30:
                    snippet = extract[:700]
                    if len(extract) > 700:
                        snippet += '...'
                    answer = (f"📖 {title}\n\n{snippet}"
                               f"\n\n📚 来源：维基百科")
                    return jsonify({"answer": answer})
        except Exception as e:
            print(f"[搜索] 维基失败: {e}", file=sys.stderr)

        # —— 第2步：DuckDuckGo 即时答案 ——
        try:
            sq2 = urllib.parse.quote(enhanced)
            ddg_url = (f"https://api.duckduckgo.com/"
                        f"?q={sq2}&format=json&no_html=1&skip_disambig=1")
            req3 = urllib.request.Request(
                ddg_url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req3, timeout=6) as resp3:
                dd = json.loads(resp3.read().decode('utf-8', errors='ignore'))

            abstract = dd.get('Abstract', '')
            if abstract and len(abstract) > 30:
                title = dd.get('Heading', query)
                source = dd.get('AbstractSource', 'DuckDuckGo')
                snippet = abstract[:700]
                if len(abstract) > 700:
                    snippet += '...'
                answer = f"📖 {title}\n\n{snippet}\n\n📚 来源：{source}"
                return jsonify({"answer": answer})

            # 尝试 RelatedTopics
            related = dd.get('RelatedTopics', [])
            texts = [t.get('Text', '') for t in related if t.get('Text')][:3]
            if texts:
                joined = '\n\n'.join([t[:300] for t in texts])
                answer = f"🔍 相关信息：\n\n{joined}"
                return jsonify({"answer": answer})
        except Exception as e:
            print(f"[搜索] DDG失败: {e}", file=sys.stderr)

        # —— 第3步：均未找到 ——
        return jsonify({"answer":
            "🤔 未找到相关内容\n\n"
            "💡 建议尝试这些关键词：\n"
            "• 西伯利亚 地形\n"
            "• 西伯利亚 气候 冻土\n"
            "• 西伯利亚大铁路 建设\n"
            "• 俄罗斯 人口分布 城市\n"
            "• 贝加尔湖 地理位置"
        })

    except Exception as e:
        return jsonify({"answer": f"搜索失败: {str(e)}"})

# ============ 静态文件服务 ============

@app.route('/')
def index():
    """首页"""
    html_file = STATIC_DIR / 'index.html'
    if html_file.exists():
        return send_file(str(html_file))
    return "课堂服务器已启动！请将 index.html 放到 static 目录", 200

@app.route('/<path:filename>')
def static_files(filename):
    """其他静态文件"""
    return send_from_directory(str(STATIC_DIR), filename)

# ============ 启动 ============

def get_local_ip():
    """获取本机局域网 IP"""
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except:
        return "127.0.0.1"

def set_clipboard(text):
    """用 ctypes 将文本写入 Windows 剪贴板（无需额外依赖）"""
    if sys.platform != 'win32':
        return False
    try:
        import ctypes
        # 打开剪贴板
        if not ctypes.windll.user32.OpenClipboard(None):
            return False
        ctypes.windll.user32.EmptyClipboard()
        # 分配全局内存（UTF-16 LE 带 BOM）
        data = text.encode('utf-16-le') + b'\x00\x00'
        GMEM_MOVEABLE = 0x0002
        hmem = ctypes.windll.kernel32.GlobalAlloc(GMEM_MOVEABLE, len(data))
        if not hmem:
            ctypes.windll.user32.CloseClipboard()
            return False
        # 锁定内存并复制数据
        ptr = ctypes.windll.kernel32.GlobalLock(hmem)
        ctypes.memmove(ptr, data, len(data))
        ctypes.windll.kernel32.GlobalUnlock(hmem)
        # 设置剪贴板数据（CF_UNICODETEXT = 13）
        ctypes.windll.user32.SetClipboardData(13, hmem)
        ctypes.windll.user32.CloseClipboard()
        return True
    except Exception as e:
        try:
            ctypes.windll.user32.CloseClipboard()
        except:
            pass
        return False


def bring_console_to_front():
    """将控制台窗口置顶，确保老师看到网址"""
    if sys.platform != 'win32':
        return
    try:
        import ctypes
        hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        if hwnd:
            # SW_SHOW = 5, HWND_TOPMOST = -1
            ctypes.windll.user32.SetWindowPos(hwnd, -1, 0, 0, 0, 0, 0x0001 | 0x0002)
    except:
        pass


def get_all_local_ips():
    """获取本机所有局域网 IP 地址，按优先级排序（私有IP优先）"""
    ips = []
    try:
        hostname = socket.gethostname()
        infos = socket.getaddrinfo(hostname, None, socket.AF_INET)
        for info in infos:
            ip = info[4][0]
            if ip != '127.0.0.1' and ip not in ips:
                ips.append(ip)
    except:
        pass

    # 后备方案：用 UDP 连接法
    if not ips:
        try:
            s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            s.connect(("8.8.8.8", 80))
            ip = s.getsockname()[0]
            s.close()
            if ip != '127.0.0.1':
                ips.append(ip)
        except:
            pass

    if not ips:
        ips.append("127.0.0.1")

    # 按优先级排序：私有IP（192.168.x / 10.x / 172.16-31.x）排最前面
    def ip_priority(ip):
        if ip.startswith('192.168.'):
            return 0
        if ip.startswith('10.'):
            return 1
        if ip.startswith('172.'):
            parts = ip.split('.')
            if len(parts) >= 2 and 16 <= int(parts[1]) <= 31:
                return 2
        return 9  # 其他IP（VPN/虚拟机等）排后面

    ips.sort(key=ip_priority)
    return ips


def add_firewall_rule(port):
    """自动添加 Windows 防火墙入站规则，放行指定端口"""
    if sys.platform != 'win32':
        return True, "非 Windows 系统，无需配置防火墙"

    rule_name = "西伯利亚课堂服务器"

    # 先检查规则是否已存在
    try:
        result = os.popen(
            f'netsh advfirewall firewall show rule name="{rule_name}" 2>&1'
        ).read()
        if "未找到" not in result and "does not" not in result.lower():
            return True, "防火墙规则已存在"
    except:
        pass

    # 添加防火墙规则（需要管理员权限）
    try:
        cmd = (
            f'netsh advfirewall firewall add rule '
            f'name="{rule_name}" '
            f'dir=in action=allow '
            f'protocol=TCP '
            f'localport={port} '
            f'profile=any '
        )
        result = os.popen(f'{cmd} 2>&1').read()
        if "确定" in result or "Ok" in result.lower():
            return True, f"防火墙规则已添加（端口 {port}）"
        else:
            return False, f"添加防火墙规则失败: {result.strip()}"
    except Exception as e:
        return False, f"无法添加防火墙规则: {e}"




def print_banner(all_ips, port, fw_ok, fw_msg):
    """打印启动横幅，all_ips 已按优先级排序"""

    try:
        # 尝试设置控制台编码以支持 Unicode
        if sys.platform == 'win32':
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    except:
        pass

    # 推荐 IP = 第一个（优先级最高）
    recommended_ip = all_ips[0] if all_ips else "127.0.0.1"
    recommended_url = f"http://{recommended_ip}:{port}"

    lines = [
        "",
        "=" * 64,
        "  [*] 透过西伯利亚大铁路看俄罗斯 · 课堂服务器",
        "=" * 64,
        "  [OK] 服务器已启动！",
        "",
        "  ★★★ 复制这条链接发给学生 ★★★",
        f"     👉 {recommended_url} 👈",
        "",
    ]

    # 如果有两个以上 IP，列出其他的供备用
    private_ips = [ip for ip in all_ips if not (
        ip.startswith('169.254.') or  # 链路本地
        ip.startswith('127.') or       # 回环
        ip.count('.') != 3             # 非法
    )]
    if len(private_ips) > 1:
        lines += [
            "  （如果上方链接打不开，备用链接：）",
        ]
        for ip in private_ips[1:]:
            lines.append(f"     http://{ip}:{port}")
        lines.append("")

    lines += [
        "  [老师] 本机预览：",
        f"     http://localhost:{port}",
        "",
        "  打开网页后，顶部金色横幅点 📋复制链接",
        "",
    ]

    # 防火墙状态
    if fw_ok:
        lines += [f"  [防火墙] {fw_msg}", ""]
    else:
        lines += [
            f"  [防火墙] {fw_msg}",
            "  ⚠️  请以管理员身份重新运行本程序！",
            "     （右键 → 以管理员身份运行）",
            "",
        ]

    lines += [
        "  === 手机打不开？排查步骤 ===",
        "  ① 确认手机和电脑连的是同一个 WiFi",
        "  ② 试试用手机浏览器直接输入上方链接",
        "  ③ 如果还是打不开 → 老师开手机热点，",
        "     让学生连老师的热点 WiFi 再试",
        "",
        "  === 操作步骤 ===",
        "  1. 老师电脑和学生 Pad 连同一 WiFi（或老师热点）",
        "  2. 复制上方 ★★★ 链接发到学习通",
        "  3. 学生用 Pad 浏览器打开链接",
        "  4. 选择小组 → 上传图片 / 答题 / 提交成果",
        "",
        "  ⚠️  下课时按 Ctrl+C 关闭",
        "=" * 64,
    ]

    for line in lines:
        try:
            print(line)
        except UnicodeEncodeError:
            print(line.encode('ascii', errors='replace').decode('ascii'))

    print()

# 模块导入时自动加载已有数据（gunicorn 兼容）
load_results()

if __name__ == '__main__':

    if IS_CLOUD:
        # ===== 云部署模式 =====
        print(f"\n  [云部署] 透过西伯利亚大铁路看俄罗斯 · 课堂服务器")
        print(f"  [OK] 端口: {PORT}")
        print(f"  [OK] 公网可访问！\n")
        app.run(host='0.0.0.0', port=PORT, debug=False, threaded=True)
    else:
        # ===== 本地模式（原有逻辑） =====

        # 自动选择可用端口（避免冲突）
        def find_free_port(start_port):
            for p in range(start_port, start_port + 20):
                try:
                    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                    s.bind(('0.0.0.0', p))
                    s.close()
                    return p
                except OSError:
                    continue
            return None

        actual_port = find_free_port(PORT)
        if actual_port is None:
            print("\n[错误] 无法找到可用端口，请关闭其他程序后重试")
            sys.exit(1)

        all_ips = get_all_local_ips()

        # 自动添加 Windows 防火墙规则
        fw_ok, fw_msg = add_firewall_rule(actual_port)

        bring_console_to_front()  # 控制台置顶，确保老师能看到网址
        print_banner(all_ips, actual_port, fw_ok, fw_msg)

        # 自动打开浏览器（延迟3秒，先让老师看到控制台网址）
        def open_browser():
            time.sleep(3)
            import webbrowser
            webbrowser.open(f"http://localhost:{actual_port}")
            # 浏览器打开后，把控制台重新置顶，确保老师看到网址
            time.sleep(1)
            bring_console_to_front()

        t = threading.Thread(target=open_browser, daemon=True)
        t.start()

        app.run(host='0.0.0.0', port=actual_port, debug=False, threaded=True)
