// 首次使用引导与「快速开始」。
//
// 为什么单独成文件：这一层只回答两个问题——"第一次打开该做什么"和"某个功能在哪儿"。
// 它不参与阅读、检索或模型逻辑；任何一步 DOM 出问题都不应该挡住主界面，因此所有
// 挂载动作都包在 try 里，最坏情况只是少一个帮助入口。
//
// 为什么不用"自动打开设置页"来做首次引导：设置页里是选项，不是任务顺序。新用户第一次
// 需要的是"导入 → （可选）配模型 → 提问"这条路径，以及知道翻译/批注/检索在哪里。
(function () {
  const FIRST_RUN_KEY = 'kanread.onboarding.firstRunDone';
  const HIDE_STEPS_KEY = 'kanread.onboarding.hideSteps';
  const HIDE_MODEL_KEY = 'kanread.onboarding.hideModelNote';

  const readFlag = key => { try { return localStorage.getItem(key) === '1'; } catch (error) { return false; } };
  const writeFlag = key => { try { localStorage.setItem(key, '1'); } catch (error) { /* 隐私模式禁用存储时按"每次提示"处理 */ } };

  // 引导里出现的动作都复用主界面已有入口：不在帮助层里另造一套按钮逻辑，
  // 否则帮助与实际行为迟早会分叉（用户会按帮助说的做，然后发现没用）。
  function triggerImport() { const input = $('#file'); if (input) input.click(); }
  function triggerSettings() { const button = $('#settings'); if (button) button.click(); }
  function triggerTranslate() {
    const button = $('#translate-page');
    if (!state.doc) return toast('先打开一篇文献，再翻译当前页');
    if (button) button.click(); else toast('当前版本没有译文栏入口');
  }
  function focusQuestion() {
    if (!state.doc) return toast('先打开一篇文献，或直接点「导入 PDF」');
    const box = $('#question');
    if (box) box.focus();
  }
  function switchTab(name) { const button = document.querySelector('[data-tab="' + name + '"]'); if (button) button.click(); }
  function openSettingsTab(kind) {
    const button = $('#settings');
    if (button) button.click();
    // 设置对话框是异步构建的：等一拍再切栏目；切不过去就留在默认栏目，不影响使用。
    setTimeout(() => { try { if (typeof switchModelTab === 'function') switchModelTab(kind); } catch (error) { /* 保持默认栏目 */ } }, 150);
  }
  function clickIfOpen(id, hint) {
    if (!state.doc) return toast(hint);
    const button = document.getElementById(id);
    if (button) button.click(); else toast('先打开一篇文献');
  }
  function openLibraryView(view) {
    const button = document.querySelector('[data-library-view="' + view + '"]');
    if (button) button.click(); else toast('在左侧「Zotero 文献」里打开');
  }

  // 动作条目的文案必须写成"在哪儿点、会发生什么"，而不是功能名词。
  const HELP_ROWS = [
    ['导入 PDF', '点「导入 PDF」选择文件，或把文件拖进窗口。原件按字节保存，应用不会改写它。', '导入 PDF', triggerImport],
    ['配置模型（可选）', '会话模型负责问答与翻译；不配也能用本机检索、OCR、TF-IDF 向量。**启用后，问题、选区与相关原文会发送到你选择的模型服务；不启用则全部本机处理（问答与翻译不可用）。** Embedding / Rerank 是可选增强。', '打开设置', triggerSettings],
    ['解析 / 翻译 / 联网（可选）', '「识别」可接 MinerU 等解析服务——**在线解析会把页面图像或文件发到该服务**；「翻译」可接翻译服务，只发当前段原文；「联网搜索」默认关闭，搜索词逐次确认。启用哪一项，就只有该项需要的内容会发出去；都不启用则全部在本机处理。', '打开设置', triggerSettings],
    ['对选中文字提问', '在页面上选中一段原文，按 Ctrl+Enter，或点底部的「对选中文字提问」。长文献读取前会先告知预计页数与 token，确认后才开始。', '定位提问框', focusQuestion],
    ['翻译整页', '打开文献后点工具栏「⇄ 翻译整页」，原文与译文并排；只翻一段时选好文字再用。', '翻译当前页', triggerTranslate],
    ['批注、笔记与检索', '选中文字可高亮/批注；右侧「笔记」「批注」页签集中查看；「检索」页签可在全部文献里找。', '打开检索', () => switchTab('search')],
    ['屏蔽页眉等无关区域', '打开文献后点工具栏「屏蔽无关区域」，框住页眉、页脚或页码，选择"本页"或"所有页"；被屏蔽的文字不参与提问、检索与引用定位。', '', null],
    ['导出带批注的 PDF', '打开文献后在「批注」页签点「⇩ 导出带批注的 PDF 副本」：生成一份新副本，原文件不改；旋转页上的批注不会写入副本，界面会提示数量。', '去批注页签', () => { switchTab('annotations'); toast('在「批注」页签里点「导出带批注的 PDF 副本」'); }],
    ['从 Zotero 迁移批注', '先在「Zotero 文献」页打开附件，再到「批注」页签点「⇩ 从 Zotero 导入批注」：分批预览、去重，只读 Zotero、不改动它。', '去 Zotero 文献', () => openLibraryView('zotero')],
    ['文档导航索引（试点）', '打开文献后点工具栏「▦ 导航索引」：只为"该读哪几页"给建议，不参与回答与证据、不调用模型；默认关闭，可随时删除。', '打开导航面板', () => clickIfOpen('doc-navigation', '先打开一篇文献')],
    ['备份、诊断与更新', '设置 →「数据与更新」：一键备份 data/、导出脱敏诊断包、复制更新命令。更新只替换程序文件，不会动 data/ 与 .venv/。', '打开数据与更新', () => openSettingsTab('maintenance')],
  ];

  let helpDialog = null;
  let statusLine = null;

  async function refreshHelpStatus() {
    if (!statusLine) return;
    try {
      const status = await api('/status');
      statusLine.textContent = status.ai
        ? '会话模型已配置：' + status.model + '。问答与翻译会把问题、选区与相关原文发送到该服务。'
        : '还没有配置会话模型：本机检索、OCR、批注与笔记可直接使用；问答与翻译需要模型。';
    } catch (error) {
      statusLine.textContent = '暂时读不到模型配置状态；不影响本机检索与阅读。';
    }
  }

  function buildHelpDialog() {
    helpDialog = el('dialog', 'onboarding-help');
    helpDialog.id = 'quick-start-dialog';
    helpDialog.setAttribute('aria-label', '快速开始与功能位置');
    const head = el('div', 'onboarding-help-head');
    head.append(el('h2', '', '快速开始'), el('p', 'hint', '这里只放"第一次怎么走通"和常用功能的位置；完整选项在「设置」里。'));
    helpDialog.append(head);
    statusLine = el('p', 'onboarding-status', '正在读取模型配置…');
    helpDialog.append(statusLine);
    const list = el('div', 'onboarding-help-list');
    for (const [title, description, actionLabel, action] of HELP_ROWS) {
      const row = el('div', 'onboarding-help-row');
      const text = el('div', 'onboarding-help-text');
      text.append(el('strong', '', title), el('p', 'hint', description));
      row.append(text);
      if (actionLabel && action) {
        const button = el('button', 'secondary', actionLabel);
        button.type = 'button';
        button.onclick = async () => {
          try { await action(); } catch (error) { toast(error.message); }
          if (title !== '屏蔽页眉等无关区域') helpDialog.close();
        };
        row.append(button);
      }
      list.append(row);
    }
    helpDialog.append(list);
    const actions = el('div', 'dialog-actions');
    const close = el('button', 'primary', '知道了');
    close.type = 'button';
    close.onclick = () => helpDialog.close();
    actions.append(close);
    helpDialog.append(actions);
    helpDialog.addEventListener('close', () => { helpDialog.hidden = true; });
    document.body.append(helpDialog);
  }

  function openHelp() {
    if (!helpDialog) buildHelpDialog();
    // dialog 用一次就保留：反复开关比每次重建省事，也不会残留多个对话框中叠。
    helpDialog.hidden = false;
    refreshHelpStatus();
    helpDialog.showModal();
  }

  function mountHeaderEntry() {
    const right = document.querySelector('.header-right');
    if (!right || $('#quick-start')) return;
    const button = el('button', 'icon-button', '? 快速开始');
    button.id = 'quick-start';
    button.type = 'button';
    button.title = '三步开始、常用功能在哪儿';
    button.onclick = openHelp;
    right.insertBefore(button, right.querySelector('#settings'));
  }

  function buildFirstRunDialog() {
    const dialog = el('dialog', 'onboarding-first-run');
    dialog.id = 'onboarding-first-run';
    dialog.setAttribute('aria-label', '第一次使用勘读');
    dialog.append(el('h2', '', '三步开始'));
    dialog.append(el('p', 'hint', '不用先看说明：下面三步就是完整路径。模型可以以后再配——本机检索、OCR、批注和笔记不依赖它。'));
    const steps = el('ol', 'onboarding-ol');
    const step = (title, text) => { const item = el('li'); item.append(el('strong', '', title), el('p', 'hint', text)); steps.append(item); };
    step('导入一份 PDF', '点下面的按钮选择文件。原件按字节保存到应用数据目录，不会改写你的文件。');
    step('（可选）选用模型', '设置 →「会话模型」：在「模型服务」添加平台后，回用途页选择模型并保存。**配置后，问题、选区与相关原文会发送到该服务；不配则全部本机处理，问答与翻译不可用。**');
    step('选中文字提问', '选中原文按 Ctrl+Enter；长文献会先告知预计读取量，确认后才开始。回答里的引用编号点一下就能跳回原页。');
    dialog.append(steps);
    dialog.append(el('p','hint','说明：本应用默认在本机运行，但不是断网软件——只有你启用的模型/解析/翻译/联网服务会收到相应内容；不启用任何外部服务时全部在本机处理。'));
    const actions = el('div', 'dialog-actions');
    const later = el('button', 'secondary', '我自己探索');
    later.type = 'button';
    later.onclick = () => { writeFlag(FIRST_RUN_KEY); dialog.close(); };
    const start = el('button', 'primary', '导入第一份 PDF');
    start.type = 'button';
    start.onclick = () => { writeFlag(FIRST_RUN_KEY); dialog.close(); triggerImport(); };
    actions.append(later, start);
    dialog.append(actions);
    dialog.addEventListener('close', () => { dialog.hidden = true; });
    return dialog;
  }

  function syncStepsCard() {
    const panel = $('#library-panel');
    if (!panel) return;
    const empty = !state.docs || state.docs.length === 0;
    let card = $('#onboarding-steps');
    if (!empty || readFlag(HIDE_STEPS_KEY)) {
      if (card) card.remove();
      return;
    }
    if (card) return;
    card = el('div', 'onboarding-steps');
    card.id = 'onboarding-steps';
    const head = el('div', 'onboarding-steps-head');
    head.append(el('strong', '', '第一次使用？三步开始'));
    const hide = el('button', 'link-button', '不再显示');
    hide.type = 'button';
    hide.onclick = () => { writeFlag(HIDE_STEPS_KEY); card.remove(); };
    head.append(hide);
    card.append(head);
    const list = el('ol', 'onboarding-ol');
    const step = (title, text) => { const item = el('li'); item.append(el('strong', '', title), el('span', 'hint', text)); list.append(item); };
    step('导入 PDF', '导入后即可阅读、检索、批注；原文不会被改写。');
    step('配置模型（可选）', '设置 →「会话模型」选择模型并保存；问答与翻译需要模型。');
    step('选中文字提问', '选中原文按 Ctrl+Enter，回答里的引用编号可跳回原页。');
    card.append(list);
    const actions = el('div', 'onboarding-actions');
    const start = el('button', 'primary', '导入第一份 PDF');
    start.type = 'button';
    start.onclick = triggerImport;
    const help = el('button', 'secondary', '看看功能都在哪');
    help.type = 'button';
    help.onclick = openHelp;
    actions.append(start, help);
    card.append(actions);
    panel.prepend(card);
  }

  function syncModelNote() {
    const welcome = $('#chat-tab');
    if (!welcome) return;
    const badge = $('#model-badge');
    const configured = badge && badge.textContent && badge.textContent !== '未配置模型' && badge.textContent !== '离线';
    let note = $('#onboarding-model-note');
    if (configured || readFlag(HIDE_MODEL_KEY)) { if (note) note.remove(); return; }
    if (note) return;
    note = el('div', 'onboarding-note');
    note.id = 'onboarding-model-note';
    note.setAttribute('role', 'status');
    note.append(el('span', '', '还没有配置会话模型：本机检索与 OCR 可用，问答与翻译需要模型。'));
    const open = el('button', 'link-button', '去配置');
    open.type = 'button';
    open.onclick = triggerSettings;
    const dismiss = el('button', 'link-button', '忽略');
    dismiss.type = 'button';
    dismiss.onclick = () => { writeFlag(HIDE_MODEL_KEY); note.remove(); };
    note.append(open, dismiss);
    welcome.prepend(note);
  }

  function afterInit() {
    mountHeaderEntry();
    syncStepsCard();
    syncModelNote();
    // 文献数量与模型徽标都由其它脚本更新，这里只观察结果，不覆盖它们的函数
    // （本项目曾因多处 monkey-patch 覆盖全局函数而出现"改了却没生效"的叠加问题）。
    const documents = $('#documents');
    if (documents) new MutationObserver(() => syncStepsCard()).observe(documents, { childList: true });
    const badge = $('#model-badge');
    if (badge) new MutationObserver(() => syncModelNote()).observe(badge, { childList: true, characterData: true, subtree: true });
    if (!readFlag(FIRST_RUN_KEY) && (!state.docs || state.docs.length === 0) && !window.__TRANSLATION_WINDOW) {
      const dialog = buildFirstRunDialog();
      document.body.append(dialog);
      // 等主界面自己的首屏渲染完成再弹，避免与 init 里的 toast/文库绘制抢同一帧。
      setTimeout(() => { try { if (dialog.isConnected && !dialog.open) dialog.showModal(); } catch (error) { /* 已有其它对话框打开时不抢焦点 */ } }, 600);
    }
  }

  // 只挂一个钩子：boot.js 在全部脚本加载完才调用 init()，这里包一层等它结束后再挂引导。
  // 如果主界面初始化失败，引导也不应该假装成功，所以 await 后仍由 try 兜住。
  if (typeof window.init === 'function') {
    const originalInit = window.init;
    window.init = async function () {
      try { return await originalInit.apply(this, arguments); }
      finally { try { afterInit(); } catch (error) { /* 引导失败不影响主界面 */ } }
    };
  }

  // 供 node 测试直接验证纯逻辑（不依赖 DOM）。
  window.__kanreadOnboarding = { HELP_ROWS, readFlag, writeFlag };
})();
