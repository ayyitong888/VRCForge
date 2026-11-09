import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

const { chromium } = createRequire(import.meta.url)(process.env.VRCFORGE_PLAYWRIGHT_PATH || 'playwright');
const bundle = await build({ stdin: { resolveDir: process.cwd(), loader: 'tsx', contents: `
import {useState} from 'react';
import {createRoot} from 'react-dom/client';
import {mockIPC,mockWindows} from '@tauri-apps/api/mocks';
import i18n from 'i18next';
import {initReactI18next} from 'react-i18next';
import {useChatSessions} from './src/hooks/use-chat-sessions';
import {useTaskbarUnreadBadge} from './src/hooks/use-taskbar-unread-badge';
await i18n.use(initReactI18next).init({lng:'en',resources:{en:{translation:{}}}});
let label, rid = 0;
const icons = new Map();
const fillText = CanvasRenderingContext2D.prototype.fillText;
CanvasRenderingContext2D.prototype.fillText = function(value, ...args) {
  label = value;
  return fillText.call(this, value, ...args);
};
mockWindows('main');
mockIPC((command, args) => {
  if (command === 'plugin:image|new') {
    if (args.rgba.length !== 32*32*4 || args.width !== 32 || args.height !== 32) throw Error('Invalid overlay bitmap');
    icons.set(++rid, label);
    return rid;
  }
  if (command === 'plugin:window|set_overlay_icon') {
    if (args.label !== 'main') throw Error('Wrong window');
    window.badge = args.value === undefined ? '' : icons.get(args.value);
  }
  if (command === 'plugin:resources|close') icons.delete(args.rid);
});
let focused = false;
document.hasFocus = () => focused;
window.focusChatWindow = () => { focused = true; window.dispatchEvent(new Event('focus')); };
const noop = () => {}, paths = [];
const chat = (id) => ({id, sessionId:id, projectPath:'', projectType:'general', revision:0, items:[{id:id+'-user',type:'user',text:'start'}]});
const reply = (id) => ({id, type:'agent', response:{plan:{reply:id,nextStep:'done'}}});
function Fixture() {
  const [activeView, setActiveView] = useState('chat');
  const [deliveries, setDeliveries] = useState([]);
  const [approvals, setApprovals] = useState([]);
  const [questions, setQuestions] = useState([]);
  const [externalConfirmationCount, setExternalConfirmationCount] = useState(0);
  const sessions = useChatSessions({endpoint:location.origin, activeView, runtimeConnected:false,
    projectPrefsReady:true, projectPaths:paths, customProjectPaths:paths, activeProjectPath:'',
    setActiveProjectPath:noop, setActiveView, setError:noop, expandProjectGroup:noop,
    initialChatState:{chats:[chat('a'),chat('b')],activeChatId:'a'}});
  useTaskbarUnreadBadge({ chats:sessions.chats, deliveries, approvals, questions, externalConfirmationCount });
  window.fixture = {sessions, setActiveView, setDeliveries, setApprovals, setQuestions, setExternalConfirmationCount, reply};
  return null;
}
createRoot(document.getElementById('root')).render(<Fixture/>);
` }, bundle: true, write: false, format: 'esm', jsx: 'automatic' });

const browser = await chromium.launch({ headless: true, channel: process.env.VRCFORGE_BROWSER_CHANNEL || 'msedge' });
try {
  const page = await browser.newPage({ userAgent: 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)' });
  await page.route('http://127.0.0.1/**', (route) => route.fulfill({ contentType: 'text/html', body: '<div id="root"></div>' }));
  await page.goto('http://127.0.0.1');
  await page.addScriptTag({ type: 'module', content: bundle.outputFiles[0].text });
  const expectBadge = (value) => page.waitForFunction((label) => window.badge === label, value);
  await expectBadge('');
  await page.evaluate(() => {
    const { sessions, setDeliveries, reply } = window.fixture;
    sessions.appendToChat('a', reply('a1'));
    sessions.appendToChat('a', { id:'user2', type:'user', text:'follow-up' });
    sessions.appendToChat('a', reply('a2'));
    sessions.appendToChat('b', reply('b1'));
    sessions.appendToChat('b', reply('b2'));
    const delivery = { deliveryId:'delivery-b2', chatId:'b', agentItemId:'b2' };
    setDeliveries([delivery, { ...delivery }]); // Overlap while acknowledging a different chat.
  });
  await expectBadge('2');
  assert.deepEqual(await page.evaluate(() => window.fixture.sessions.chats.map((chat) => chat.lastViewedAt ?? null)), [null, null]);
  await page.evaluate(() => window.focusChatWindow());
  await expectBadge('1');
  await page.evaluate(() => window.fixture.setActiveView('settings'));
  await page.evaluate(() => window.fixture.sessions.appendToChat('a', window.fixture.reply('a3')));
  await expectBadge('2');
  await page.evaluate(() => {
    const { sessions, setDeliveries } = window.fixture;
    sessions.openChat(sessions.chats[1]);
    setDeliveries([]); // Existing background delivery acknowledgement.
  });
  await expectBadge('1');
  assert.deepEqual(await page.evaluate(() => window.fixture.sessions.chats.map((chat) => chat.lastViewedAt)), ['a2', 'b2']);
  await page.evaluate(() => window.fixture.sessions.openChat(window.fixture.sessions.chats[0]));
  await expectBadge('');

  await page.evaluate(() => {
    const { setApprovals, setDeliveries, setQuestions } = window.fixture;
    const external = { id:'mcp-approval', status:'pending', approvalChannel:'external_mcp', projectRoot:'another-project' };
    setApprovals([external, {...external}, { id:'old', status:'rejected' }]);
    setQuestions([
      { questionId:'blocking', sessionId:'b', status:'pending', needsUserAction:true },
      { questionId:'optional', sessionId:'a', status:'pending', needsUserAction:false },
    ]);
    setDeliveries([{ deliveryId:'waiting', chatId:'b', approvalId:external.id }]);
  });
  await expectBadge('2');
  await page.evaluate(() => {
    window.fixture.setApprovals([{ id:'mcp-approval', status:'approved' }]);
    window.fixture.setDeliveries([]);
  });
  await expectBadge('1');
  await page.evaluate(() => window.fixture.setQuestions([{ questionId:'blocking', status:'answered' }]));
  await expectBadge('');
  await page.evaluate(() => window.fixture.setExternalConfirmationCount(1));
  await expectBadge('1');
  await page.evaluate(() => window.fixture.setExternalConfirmationCount(0));
  await expectBadge('');
} finally {
  await browser.close();
}
