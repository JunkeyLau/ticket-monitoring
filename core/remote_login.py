"""In-memory, single-use bridge for a user-operated login browser.

Playwright objects remain on the owning browser thread. Requests communicate
through a bounded queue and never write screenshots or keystrokes to disk.
"""
import base64
import hmac
import queue
import secrets
import threading
import time


class RemoteLogin:
    def __init__(self):
        self.token = secrets.token_urlsafe(32)
        self.requests = queue.Queue(maxsize=8)
        self.closed = threading.Event()
        self.cancelled = threading.Event()

    def call(self, token, action, payload=None):
        if not isinstance(token, str) or not hmac.compare_digest(self.token, token):
            raise PermissionError('登录会话无效')
        if self.closed.is_set():
            raise RuntimeError('登录会话已结束')
        response = queue.Queue(maxsize=1)
        try:
            self.requests.put_nowait((action, payload or {}, response))
            result = response.get(timeout=20)
        except queue.Full:
            raise RuntimeError('操作过于频繁')
        except queue.Empty:
            raise RuntimeError('浏览器响应超时')
        if isinstance(result, Exception):
            raise RuntimeError('浏览器操作失败')
        return result

    def serve(self, ctx, page, done, timeout=600):
        deadline = time.monotonic() + timeout
        while not done.is_set():
            if self.cancelled.is_set():
                raise RuntimeError("登录已取消")
            if time.monotonic() >= deadline:
                raise RuntimeError('登录窗口超时，请重新打开')
            try:
                action, payload, response = self.requests.get(timeout=0.2)
            except queue.Empty:
                # Pump Playwright events, including popup notifications.
                pages = [p for p in ctx.pages if not p.is_closed()]
                if not pages:
                    raise RuntimeError("登录窗口已关闭")
                page = pages[-1]
                page.wait_for_timeout(1)
                continue
            try:
                pages = [p for p in ctx.pages if not p.is_closed()]
                if not pages:
                    raise RuntimeError('登录窗口已关闭')
                page = pages[-1]
                if action == 'frame':
                    img = page.screenshot(type='jpeg', quality=65, timeout=10000)
                    result = {'image': base64.b64encode(img).decode('ascii'),
                              'viewport': page.viewport_size}
                elif action in ('click', 'down', 'move', 'up'):
                    v = page.viewport_size
                    x, y = float(payload['x']), float(payload['y'])
                    if not (0 <= x < v['width'] and 0 <= y < v['height']):
                        raise ValueError('坐标超出范围')
                    page.mouse.move(x, y)
                    if action == "click":
                        page.mouse.click(x, y)
                    elif action == "down":
                        page.mouse.down()
                    elif action == "up":
                        page.mouse.up()
                    result = {}
                elif action == 'text':
                    value = payload.get('text')
                    if not isinstance(value, str) or len(value) > 512:
                        raise ValueError('输入过长')
                    page.keyboard.insert_text(value)
                    result = {}
                elif action == 'key':
                    key = payload.get('key')
                    if key not in ('Enter', 'Tab', 'Backspace', 'Delete', 'Escape',
                                   'ArrowLeft', 'ArrowRight', 'ArrowUp', 'ArrowDown'):
                        raise ValueError('不支持的按键')
                    page.keyboard.press(key)
                    result = {}
                elif action == 'scroll':
                    delta = max(-1000, min(1000, int(payload.get('delta', 0))))
                    page.mouse.wheel(0, delta)
                    result = {}
                else:
                    raise ValueError('不支持的操作')
                response.put(result)
            except Exception as exc:
                response.put(exc)

    def close(self):
        self.closed.set()
        while True:
            try:
                _, _, response = self.requests.get_nowait()
                response.put(RuntimeError('登录会话已结束'))
            except queue.Empty:
                break
