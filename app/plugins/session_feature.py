"""Internal session feature. Explicit host services; no external plugin loading.

只做一件事：让后台知道"还有没有打开的阅读窗口"，从而在窗口关闭后自行退出
（见 `app/session_guard.py`）。这些接口只监听本机回环地址，正文里不携带任何内容。
"""
import asyncio


def install(router, host):
    from app import session_guard

    @router.post('/api/session/ping')
    def session_ping():
        session_guard.touch()
        return {'ok': True, 'keep_server': not session_guard.enabled()}

    @router.post('/api/session/close')
    def session_close():
        # 页面卸载时用 sendBeacon 调用：正文可能是空的 text/plain，这里不需要读取内容。
        session_guard.closed()
        return {'ok': True}

    @router.get('/api/session/state')
    def session_state():
        """只读状态：界面与排查都用得上（不含任何用户内容）。"""
        return session_guard.state()

    @router.get('/api/session/stream')
    async def session_stream(request: host.Request):
        """长连接：窗口一关，连接就断，后台立刻知道（定时器在后台页面会被限流甚至冻结）。

        只发注释行做保活，不推送任何数据；断开时在 finally 里销账。
        """
        from fastapi.responses import StreamingResponse

        async def events():
            session_guard.connect()
            try:
                yield 'retry: 2000\n\n'
                while True:
                    if await request.is_disconnected():
                        break
                    yield ': alive\n\n'
                    await asyncio.sleep(10)
            finally:
                session_guard.disconnect()

        return StreamingResponse(events(), media_type='text/event-stream',
                                 headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})
    return {'session_state': session_state}
