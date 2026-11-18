import assert from "node:assert/strict";
import { build } from "esbuild";
import { chromium } from "playwright";

const bundle = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import { createRoot } from "react-dom/client";
    import { initializeI18n } from "./src/i18n";
    import { useCheckpointWorkspaceController } from "./src/hooks/use-checkpoint-workspace-controller";
    import { useDoctorFixController } from "./src/hooks/use-doctor-fix-controller";
    import { CheckpointWorkspace } from "./src/components/checkpoints/checkpoint-workspace";
    import { DoctorWorkspace } from "./src/components/doctor/doctor-workspace";
    const noop = () => {};
    window.calls = [];
    const checkpoint = {id:"adjust-1", label:"Face A", kind:"face", status:"ready"};
    const recovery = {id:"recovery-1", checkpointId:"base-1", targetTool:"Apply materials"};
    window.__TAURI_INTERNALS__ = {invoke: async (command, {request}={}) => {
      window.calls.push({command,request});
      return {ok:true, checkpoint, checkId:"service", status:"repaired", checkpoints:[], recoveries:[]};
    }};
    function Fixture() {
      const checks = useCheckpointWorkspaceController({endpoint:"fixture", activeView:"chat", runtimeConnected:false,
        activeProjectPath:"", refresh:noop, setError:noop});
      const doctor = useDoctorFixController({ endpoint:"fixture", projectPath:"", onRefresh:noop, onMessage:noop, onError:noop });
      return <><CheckpointWorkspace checkpoints={[]} interruptedRecoveries={[recovery]} adjustmentCheckpoints={[checkpoint]}
        selectedProjectPath="" recoveryBusyId={checks.recoveryBusyId} adjustmentBusyId={checks.adjustmentBusyId} onRenameAdjustment={checks.renameAdjustment} onDeleteAdjustment={checks.removeAdjustment}
        onOverwriteAdjustment={checks.overwriteAdjustment} onResolveRecovery={checks.resolveRecovery} />
        <DoctorWorkspace report={{ok:false,checks:[{id:"service",title:"Local service",status:"error",fixable:true,fixModes:["safe","force"]}]}}
          chatRecoveries={[]} onFix={doctor.fixCheck} formatPayload={JSON.stringify} /></>;
    }
    initializeI18n().then(()=>createRoot(document.querySelector("#root")).render(<Fixture />));
  `}, jsx: "automatic", bundle: true, write: false,
});
const browser = await chromium.launch({
  channel: process.env.VRCFORGE_BROWSER_CHANNEL || (process.platform === "win32" ? "msedge" : undefined),
  headless: true,
});
try {
  const page = await browser.newPage({ locale: "en-US" });
  page.setDefaultTimeout(4000);
  const errors = [];
  page.on("pageerror", error => errors.push(error.message));
  const scripts = [];
  page.on("dialog", async dialog => { scripts.push(dialog.type()); await dialog.dismiss(); });
  await page.setContent('<div id="root"></div>');
  await page.addScriptTag({content: bundle.outputFiles[0].text});
  await page.locator("summary").filter({hasText:"Adjustment Timeline"}).first().click();
  for (const [label, command] of [
    ["Rename checkpoint","update_adjustment_checkpoint"], ["Overwrite checkpoint","overwrite_adjustment_checkpoint"],
    ["Delete checkpoint","delete_adjustment_checkpoint"], ["Mark as resolved","resolve_interrupted_apply_recovery"],
    ["Force fix","fix_doctor_check"],
  ]) {
    const trigger = page.getByRole("button", { name: label, exact: true });
    await trigger.click();
    assert.deepEqual(scripts, [], "desktop actions should open inside the app");
    const dialog = page.getByRole("dialog");
    await dialog.waitFor();
    await page.keyboard.press("Escape");
    await dialog.waitFor({state:"detached"});
    assert.equal(await page.evaluate(c => window.calls.filter(x=>x.command===c).length, command),0);
    assert.equal(await trigger.evaluate(button=>button===document.activeElement),true);
    await trigger.click();
    if (command === "update_adjustment_checkpoint") {
      assert.equal(await dialog.getByRole("textbox").inputValue(),"Face A");
      await dialog.getByRole("textbox").fill("Face B");
    }
    await dialog.getByRole("button").last().click();
    const calls = await page.evaluate(c => window.calls.filter(x=>x.command===c),command);
    assert.equal(calls.length,1,command);
    if (command === "update_adjustment_checkpoint") assert.equal(calls[0].request.body.label,"Face B");
    if (command === "resolve_interrupted_apply_recovery") assert.equal(calls[0].request.confirmResolved,true);
    if (command === "fix_doctor_check") assert.equal(calls[0].request.mode,"force");
  }
  assert.deepEqual(errors, []);
  console.log("5 desktop actions confirmed or cancelled in app; original requests and focus verified");
} finally { await browser.close(); }
