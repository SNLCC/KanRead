"""Explicit official metadata snapshots, never infer capacity from a model name."""
import copy
from urllib.parse import urlsplit

DEEPSEEK_SOURCE='https://api-docs.deepseek.com/quick_start/pricing/'

# 语音模型快照。用户报告的第 2 项：MiniMax 平台确实提供语音模型，但"获取模型"拿不到。
# 原因是平台自己的 `/v1/models` 只列对话模型（MiniMax 官方文档的列表接口示例里只有对话
# 模型；MiMo 同理不保证列出全部语音模型）。这些 id 与用途只能按官方文档逐个登记，
# 不能靠名称猜测——猜出来的能力会让用户选中一个跑不通的组合。
#
# 每条都带官方来源链接，界面会显示"官方来源"供用户核对；快照日期写进 source 标记，
# 表示这是**某一天**的核对结果，平台改模型时用户可以自行在「模型能力」里标注覆盖。
SPEECH_SNAPSHOT='2026-09-18'
MIMO_ASR_SOURCE='https://mimo.mi.com/docs/zh-CN/api/audio/Speech-Recognition'
MIMO_TTS_SOURCE='https://mimo.mi.com/docs/zh-CN/api/audio/tts'
MINIMAX_ASR_SOURCE='https://platform.minimax.io/docs/api-reference/speech-to-text'
MINIMAX_TTS_SOURCE='https://platform.minimax.io/docs/api-reference/speech-t2a-http'

def _row(model,capabilities,url):
    return {'id':model,'capabilities':capabilities,'source':f'官方文档（{SPEECH_SNAPSHOT}）','metadata_url':url}

SPEECH_MODELS={
    'api.xiaomimimo.com':[
        _row('mimo-v2.5-asr',['asr'],MIMO_ASR_SOURCE),
        _row('mimo-v2.5-tts',['tts'],MIMO_TTS_SOURCE),
        _row('mimo-v2.5-tts-voicedesign',['tts'],MIMO_TTS_SOURCE),
        _row('mimo-v2.5-tts-voiceclone',['tts'],MIMO_TTS_SOURCE),
    ],
    # 国内与国际站是两套域名、同一套接口与模型 id。
    'api.minimax.io':[
        _row('asr-1.0',['asr'],MINIMAX_ASR_SOURCE),
        *[_row(model,['tts'],MINIMAX_TTS_SOURCE) for model in
          ('speech-2.8-hd','speech-2.8-turbo','speech-2.6-hd','speech-2.6-turbo',
           'speech-02-hd','speech-02-turbo','speech-01-hd','speech-01-turbo')],
    ],
}
SPEECH_MODELS['api.minimaxi.com']=SPEECH_MODELS['api.minimax.io']


def speech_models(base):
    """该服务地址上有官方文档可核对的语音模型（没有就返回空列表）。

    按主机名取，而不是按用户在面板里选的平台 id：用户完全可以把一个"自定义"连接
    指向同一台主机，那时同样应该看到这些模型。
    """
    host=(urlsplit(base or '').hostname or '').lower()
    return copy.deepcopy(SPEECH_MODELS.get(host,[]))


def official(base,model):
    if urlsplit(base).hostname=='api.deepseek.com' and model in ('deepseek-flash','deepseek-v4-flash','deepseek-v4-flash-vision-exp','deepseek-v4-pro'):
        return {'context_window':1000000,'max_output':393216,'capabilities':['chat','image_input'] if model!='deepseek-v4-pro' else ['chat'],
                'source':'官方文档（2026-09-16）','metadata_url':DEEPSEEK_SOURCE}
    row=next((item for item in speech_models(base) if item['id']==model),None)
    if row:return {k:v for k,v in row.items() if k!='id'}
    return {}

def effective(profile):
    if profile.get('capacity_mode','auto')=='manual':return profile
    meta=official(profile.get('base_url',''),profile.get('model',''))
    if profile.get('connection_id'):
        from .catalog import get
        row=next((r for r in get(profile['connection_id']).get('models',[]) if r['id']==profile.get('model')),{})
        if row.get('context_window'):meta={**meta,**row}
    if meta.get('max_output') and profile.get('output_reserve',4096)<16384:
        profile={**profile,'output_reserve':16384}
    if meta.get('context_window'):
        profile={**profile,'context_window':meta['context_window'],'capacity_source':meta.get('source','平台元数据'),'metadata_url':meta.get('metadata_url','')}
    else:profile={**profile,'capacity_source':'平台未提供容量，使用保守值（可手动设置）'}
    return profile
