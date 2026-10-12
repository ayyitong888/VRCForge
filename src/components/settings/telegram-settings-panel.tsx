import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { configureTelegram, fetchTelegramStatus, openTelegramBotFather, startTelegram, stopTelegram, testTelegramConnection, type TelegramStatus } from "../../lib/api/telegram";
import { Badge } from "../ui/badge";
import { Button } from "../ui/button";

const copy = {
  en: {
    addCurrentProject: "Add current project",
    network: "Telegram connection", environment: "Use environment proxy", direct: "Direct connection", custom: "Use a proxy", proxy: "Proxy address",
    proxyHelp: "Enter your proxy app's HTTP or mixed listening address, for example http://127.0.0.1:7890. Use your actual port. HTTP/HTTPS proxies only; SOCKS and URLs with passwords are not supported. This affects Telegram only.",
    environmentHelp: "Uses HTTPX environment proxy settings; this does not mean Windows system proxy is detected. Choose Use a proxy if your proxy app requires an explicit address.",
    test: "Test connection", tested: "Bot API connection verified", testHelp: "Save changes before testing. Testing checks the saved bot token and network; it does not send a chat message or start receiving messages. Disconnect before changing configuration.",
    botStep: "1. Create your bot", botGuide: "Open the official @BotFather, send /newbot, and follow its prompts to choose a name and username. Paste the token it returns below. Already have a bot? Use its existing token.",
    openBot: "Open BotFather", openFailed: "Could not open Telegram. Open https://t.me/BotFather in your browser.",
    ownerStep: "2. Identify your account", ownerGuide: "Enter your own numeric user ID, not your @username or the bot ID. If you use a third-party ID lookup bot such as @userinfobot, send only /start; never send it your bot token.",
    projectStep: "3. Allow project access (optional)", projectGuide: "Leave this list empty for ordinary conversation. To work on an avatar, add its Unity project folder containing Assets and ProjectSettings, one folder per line. This sets which projects Telegram may access. You can start chatting without selecting a project or conversation first.",
    verifyStep: "4. Save, test, then connect", verifyGuide: "Save, test the connection, then connect. Open your bot in Telegram and send an ordinary message to start a continuing conversation. When a request first needs a project, select an allowed project and continue the original request without resending it. Use the command menu or /session to create or switch conversations, including to continue a desktop conversation. /project selects a project, /model selects the current conversation’s model, and /compact organizes its context. /status shows progress, context usage and permissions; /permissions changes this computer's permission mode; /stop requests a stop. Messages during a task add instructions. /help shows all operations, including approvals, questions and previews. Requests that need approval arrive as cards.",
    title: "Telegram remote access", help: "This computer and VRCForge must stay online. Only your numeric Telegram user ID in a private chat is allowed. Operations follow this computer's permission settings; requests that need approval arrive as cards.",
    token: "Bot token", tokenHint: "Leave blank to keep the saved token.", owner: "Owner Telegram user ID", projects: "Allowed project paths (optional; one exact absolute path per line)",
    tokenSaved: "Token saved", tokenDraft: "Token entered · not saved", tokenMissing: "No token entered", tokenUnknown: "Token status unavailable",
    tokenPlaceholder: "Paste the token from BotFather", tokenSavedPlaceholder: "Token saved — leave blank to keep it",
    save: "Save configuration", start: "Connect", stop: "Disconnect", refresh: "Refresh status", saved: "Configuration saved. Test the connection, then connect to start receiving messages.",
    running: "Working normally · receiving messages", configured: "Configured · disconnected", disabled: "Not configured", pending: "Connecting", unknown: "Status unavailable", failed: "Request failed. Check the local backend and Telegram connection, then refresh.",
    invalid: "Enter a positive numeric Telegram user ID and a bot token for the first configuration.", error: "Connection needs attention. Check the bot token and network, then reconnect.",
  },
  zh: {
    addCurrentProject: "添加当前工程",
    network: "Telegram 连接方式", environment: "沿用环境代理", direct: "直接连接", custom: "使用代理", proxy: "代理地址",
    proxyHelp: "填写代理软件的 HTTP 或混合代理地址，例如 http://127.0.0.1:7890，端口以你软件里显示的为准。目前支持 HTTP/HTTPS 代理，不支持 SOCKS 或带密码的地址。这里只影响 Telegram。",
    environmentHelp: "沿用程序启动时的环境代理，不代表会自动读取 Windows 系统代理。开了代理软件仍连不上时，选“使用代理”并填写地址。",
    test: "测试连接", tested: "机器人接口连接成功", testHelp: "修改后先保存，再测试。测试只检查已保存的 Token 和网络，不会发送聊天消息，也不会开始接收消息。修改配置前请先断开。",
    botStep: "1. 创建你的机器人", botGuide: "点击下方按钮，进入 Telegram 的官方 BotFather。发送 /newbot，按提示给机器人起名，再设置一个以 bot 结尾的用户名。完成后，把它发给你的整段 Token 复制到下面。已经创建过的机器人可以直接填现有 Token。",
    openBot: "打开 BotFather", openFailed: "无法打开 Telegram，请在浏览器打开 https://t.me/BotFather。",
    ownerStep: "2. 告诉它只听谁的话", ownerGuide: "这里填你自己的 Telegram 数字 ID。还不知道 ID？在 Telegram 搜索 @userinfobot，点击开始，把回复中的数字 ID 填在下面。它是第三方查询机器人，不要把 Token 发给它。这里不是填手机号、@用户名，也不是填机器人的 ID。",
    projectStep: "3. 授权可访问的工程（可选）", projectGuide: "普通聊天可以留空，只有改模等工程操作才需要授权工程。在电脑上找到你的 Unity 工程文件夹，复制路径粘贴到下面，选打开后能看到 Assets 和 ProjectSettings 的那一层，不要选里面的 Assets。多个工程每行填一个，手机只能操作这些工程。开始聊天时不必先选工程或会话。",
    verifyStep: "4. 保存 → 测试 → 连接", verifyGuide: "先保存、测试连接，再点“连接”。打开 Telegram 中自己的机器人，直接发需求或普通消息即可持续聊天。首次涉及工程操作时，按提示选择已授权工程，继续原需求，不用重发。通过命令菜单或 /session 新建、切换对话，也能继续桌面端会话；/project 选工程，/model 选当前会话模型，/compact 整理上下文。/status 查看状态、上下文用量和权限，/permissions 切换此电脑权限，/stop 请求停止；任务运行时发消息可追加要求。/help 查看审批、问答、预览等全部操作。需要审批的操作会直接发来卡片。",
    title: "Telegram 远程访问", help: "这台电脑和 VRCForge 必须保持在线。仅接受指定 Telegram 数字用户 ID 的私聊；操作遵循此电脑的权限设置，需要审批时会直接发来卡片。",
    token: "机器人 Token", tokenHint: "留空保留已保存的 Token。", owner: "所有者 Telegram 数字用户 ID", projects: "允许访问的项目路径（可选，每行一个完整绝对路径）",
    tokenSaved: "已填写 Token", tokenDraft: "Token 待保存", tokenMissing: "未填写 Token", tokenUnknown: "Token 状态不可用",
    tokenPlaceholder: "粘贴 BotFather 提供的 Token", tokenSavedPlaceholder: "已填写 Token，留空保留",
    save: "保存配置", start: "连接", stop: "断开", refresh: "刷新状态", saved: "配置已保存；请先测试连接，再点击连接开始接收消息。",
    running: "正常运作 · 正在接收消息", configured: "已配置 · 未连接", disabled: "未配置", pending: "正在连接", unknown: "状态不可用", failed: "请求失败，请检查本地后端和 Telegram 连接后刷新。",
    invalid: "请输入正整数 Telegram 用户 ID；首次配置还需填写机器人 Token。", error: "连接需要处理，请检查 Token 和网络后重新连接。",
  },
};

export function TelegramSettingsPanel({ endpoint, selectedProjectPath = "" }: { endpoint: string; selectedProjectPath?: string }) {
  const { i18n } = useTranslation();
  // Locale selects display resources only; it never affects commands or permissions.
  const text = i18n.resolvedLanguage?.startsWith("zh") ? copy.zh : copy.en;
  const [status, setStatus] = useState<TelegramStatus | null>(null);
  const [token, setToken] = useState("");
  const [owner, setOwner] = useState("");
  const [projects, setProjects] = useState("");
  const [proxyMode, setProxyMode] = useState<"environment" | "direct" | "custom">("environment");
  const [proxyUrl, setProxyUrl] = useState("");
  const [tested, setTested] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<"saved" | "failed" | "invalid" | "openFailed" | null>(null);
  const lifecycle = useRef<AbortController | null>(null);
  useEffect(() => { setTested(false); }, [token, owner, projects, proxyMode, proxyUrl, endpoint]);

  useEffect(() => {
    // This panel owns all requests; unmount/endpoint change cancels their lifetime.
    const controller = new AbortController();
    lifecycle.current = controller;
    setStatus(null);
    setToken("");
    setOwner("");
    setProjects("");
    setProxyMode("environment");
    setProxyUrl("");
    setNotice(null);
    setBusy(true);
    fetchTelegramStatus(endpoint, controller.signal).then((next) => {
      if (controller.signal.aborted) return;
      setStatus(next);
      setOwner(next.ownerId ? String(next.ownerId) : "");
      setProjects(next.projects.join("\n"));
      setProxyMode(next.proxyMode || "environment");
      setProxyUrl(next.proxyUrl || "");
    }).catch(() => {
      if (!controller.signal.aborted) setNotice("failed");
    }).finally(() => {
      if (!controller.signal.aborted) setBusy(false);
    });
    return () => controller.abort();
  }, [endpoint]);

  async function act(action: "save" | "start" | "stop" | "refresh" | "test") {
    const controller = lifecycle.current;
    if (busy || !controller || controller.signal.aborted) return;
    const ownerId = Number(owner);
    const allowed = projects.split(/\r?\n/).map((path) => path.trim()).filter(Boolean);
    if (action === "save" && (!/^\d+$/.test(owner) || !Number.isSafeInteger(ownerId) || ownerId <= 0 || (!status?.configured && !token.trim()))) {
      setNotice("invalid");
      return;
    }
    setBusy(true);
    setNotice(null);
    setTested(false);
    try {
      if (action === "test") {
        const result = await testTelegramConnection(endpoint, controller.signal);
        if (!controller.signal.aborted) {
          if (result.ok !== true) throw new Error("Connection test failed");
          setTested(true);
        }
        return;
      }
      const next = action === "save"
        ? await configureTelegram(endpoint, { token: token.trim(), ownerId, projects: allowed, proxyMode, proxyUrl: proxyMode === "custom" ? proxyUrl.trim() : "" }, controller.signal)
        : action === "start" ? await startTelegram(endpoint, controller.signal)
          : action === "stop" ? await stopTelegram(endpoint, controller.signal)
            : await fetchTelegramStatus(endpoint, controller.signal);
      if (controller.signal.aborted) return;
      setStatus(next);
      if (action === "save") {
        setToken("");
        setNotice("saved");
      }
    } catch {
      if (!controller.signal.aborted) {
        setStatus(null);
        setNotice("failed");
      }
    } finally {
      if (!controller.signal.aborted) setBusy(false);
    }
  }

  const running = status?.enabled === true && status.state === "running";
  const stateLabel = !status ? text.unknown : running ? text.running
    : status.errorCode ? text.error : status.enabled ? text.pending
      : status.configured ? text.configured : text.disabled;
  const inputClass = "w-full rounded-md border border-border bg-background px-3 py-2 text-sm";
  const tokenEntered = Boolean(token.trim());
  const tokenLabel = tokenEntered ? text.tokenDraft
    : !status ? text.tokenUnknown : status.configured ? text.tokenSaved : text.tokenMissing;
  const unsaved = Boolean(token.trim()) || owner !== String(status?.ownerId || "")
    || projects.split(/\r?\n/).map(value => value.trim()).filter(Boolean).join("\n") !== status?.projects.join("\n")
    || proxyMode !== (status?.proxyMode || "environment")
    || (proxyMode === "custom" && proxyUrl.trim() !== (status?.proxyUrl || ""));
  return (
    <section data-vrcforge-telegram className="space-y-4 rounded-xl border border-border bg-card p-4" aria-busy={busy}>
      <div className="flex flex-wrap items-center gap-2">
        <h3 className="text-sm font-semibold">{text.title}</h3>
        <Badge tone={running ? "ok" : status?.configured ? "warn" : "muted"}>{stateLabel}</Badge>
      </div>
      <p className="text-sm text-muted-foreground">{text.help}</p>
      <h4 className="text-sm font-semibold">{text.botStep}</h4>
      <p className="text-sm text-muted-foreground">{text.botGuide}</p>
      <Button type="button" variant="outline" onClick={() => void openTelegramBotFather().catch(() => {
        if (!lifecycle.current?.signal.aborted) setNotice("openFailed");
      })}>{text.openBot}</Button>
      <label className="block space-y-1 text-sm">
        <span className="flex flex-wrap items-center gap-2">
          <span>{text.token}</span>
          <Badge tone={tokenEntered || status?.configured ? "warn" : "muted"}>{tokenLabel}</Badge>
        </span>
        <input type="password" autoComplete="new-password" spellCheck={false} value={token} onChange={(event) => setToken(event.target.value)} placeholder={status?.configured ? text.tokenSavedPlaceholder : text.tokenPlaceholder} className={inputClass} disabled={busy} />
        <span className="block text-xs text-muted-foreground">{text.tokenHint}</span>
      </label>
      <h4 className="text-sm font-semibold">{text.ownerStep}</h4>
      <p className="text-sm text-muted-foreground">{text.ownerGuide}</p>
      <label className="block space-y-1 text-sm">
        <span>{text.owner}</span>
        <input inputMode="numeric" value={owner} onChange={(event) => setOwner(event.target.value)} className={inputClass} disabled={busy} />
      </label>
      <h4 className="text-sm font-semibold">{text.projectStep}</h4>
      <p className="text-sm text-muted-foreground">{text.projectGuide}</p>
      {selectedProjectPath && <Button type="button" variant="outline" disabled={busy || status?.enabled} onClick={() => {
        setProjects(value => [...new Set([...value.split(/\r?\n/).map(path => path.trim()).filter(Boolean), selectedProjectPath])].join("\n"));
      }}>{text.addCurrentProject}</Button>}
      <label className="block space-y-1 text-sm">
        <span>{text.projects}</span>
        <textarea rows={3} spellCheck={false} value={projects} onChange={(event) => setProjects(event.target.value)} className={inputClass} disabled={busy} />
      </label>
      <h4 className="text-sm font-semibold">{text.verifyStep}</h4>
      <p className="text-sm text-muted-foreground">{text.verifyGuide}</p>
      <label className="block space-y-1 text-sm">
        <span>{text.network}</span>
        <select className={inputClass} value={proxyMode} disabled={busy || status?.enabled} onChange={event => setProxyMode(event.target.value as typeof proxyMode)}>
          <option value="environment">{text.environment}</option>
          <option value="direct">{text.direct}</option>
          <option value="custom">{text.custom}</option>
        </select>
      </label>
      {proxyMode === "environment" && <p className="text-sm text-muted-foreground">{text.environmentHelp}</p>}
      {proxyMode === "custom" && <label className="block space-y-1 text-sm">
        <span>{text.proxy}</span>
        <input className={inputClass} value={proxyUrl} placeholder="http://127.0.0.1:7890" autoComplete="off" spellCheck={false} disabled={busy || status?.enabled} onChange={event => setProxyUrl(event.target.value)} />
        <span className="block text-xs text-muted-foreground">{text.proxyHelp}</span>
      </label>}
      <p className="text-sm text-muted-foreground">{text.testHelp}</p>
      <div className="flex flex-wrap gap-2">
        <Button type="button" disabled={busy || status?.enabled} onClick={() => void act("save")}>{text.save}</Button>
        <Button type="button" variant="outline" disabled={busy || !status?.configured || unsaved} onClick={() => void act("test")}>{text.test}</Button>
        <Button type="button" variant="outline" disabled={busy || !status?.configured || status.enabled} onClick={() => void act("start")}>{text.start}</Button>
        <Button type="button" variant="outline" disabled={busy || !status?.enabled} onClick={() => void act("stop")}>{text.stop}</Button>
        <Button type="button" variant="ghost" disabled={busy} onClick={() => void act("refresh")}>{text.refresh}</Button>
      </div>
      {tested && <Badge tone="ok">{text.tested}</Badge>}
      {notice && <p role="status" className={notice === "saved" ? "text-sm text-amber-700 dark:text-amber-300" : "text-sm text-destructive"}>{text[notice]}</p>}
    </section>
  );
}
