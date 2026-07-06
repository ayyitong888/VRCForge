import React from "react";
import ReactDOM from "react-dom/client";
import { initializeI18n } from "./i18n";
import "./styles.css";

async function main() {
  await initializeI18n();
  const { default: App } = await import("./App");

  ReactDOM.createRoot(document.getElementById("root") as HTMLElement).render(
    <React.StrictMode>
      <App />
    </React.StrictMode>,
  );
}

void main();
