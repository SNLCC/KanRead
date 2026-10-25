"""Internal models feature. Explicit host services; no external plugin loading."""

from .. import catalog


def install(router, host):
    @router.post('/api/settings/tokenizer')
    async def import_tokenizer(file:host.UploadFile):
        from app.capacity import import_tokenizer
        try:return import_tokenizer(await file.read(32*1024*1024+1))
        finally:await file.close()

    @router.get('/api/connections')
    def connections():return catalog.listing()
    @router.post('/api/connections')
    def add_connection(body:catalog.Connection):return catalog.save(body)
    @router.put('/api/connections/{cid}')
    def update_connection(cid:str,body:catalog.Connection):return catalog.save(body,cid)
    @router.post('/api/connections/{cid}/refresh')
    def refresh_connection(cid:str):return catalog.refresh(cid)
    @router.get('/api/connections/{cid}/impact')
    def connection_impact(cid:str):
        """删除前告知会波及哪些用途（只读预览，不改任何配置）。"""
        return {'connection_id':cid,'affected':catalog.impact(cid)}
    @router.delete('/api/connections/{cid}')
    def remove_connection(cid:str):return catalog.remove(cid)
    @router.put('/api/connections/{cid}/models')
    def mark_model(cid:str,body:catalog.ModelChoice):return catalog.mark(cid,body)
    @router.put('/api/model-assignments/{kind}')
    def assign_model(kind:str,body:catalog.Assignment):return catalog.assign(kind,body)

    @router.get('/api/settings')
    def read_settings():
        return host.settings.public_settings()

    @router.put('/api/settings')
    def update_settings(body: host.settings.SettingsInput):
        return host.settings.save(body)

    @router.put('/api/settings/purpose/{kind}')
    def update_single_purpose(kind:str,body: host.settings.ProfileInput):
        """只更新一个用途；其余用途保持原样。

        用途面板上的"直接选用内置平台预设"只需要写入一个用途，用整份 PUT /api/settings
        既要求三个用途齐备，也会把用户尚未保存的其它编辑一起提交。
        """
        return host.settings.save_purpose(kind,body)

    @router.post('/api/settings/models')
    def list_models(body: host.settings.ProbeInput):
        return host.model_services.probe(body, models=True)

    @router.post('/api/settings/test')
    def test_model(body: host.settings.ProbeInput):
        return host.model_services.probe(body)
    return {'read_settings': read_settings,'update_settings': update_settings,'update_single_purpose': update_single_purpose,'list_models': list_models,'test_model': test_model}
