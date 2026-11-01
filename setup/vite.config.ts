import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "tailwindcss";
import autoprefixer from "autoprefixer";
import sharedTailwind from "../tailwind.config";
import { fileURLToPath } from "node:url";

export default defineConfig({
  plugins:[react()],
  publicDir:false,
  css:{postcss:{plugins:[tailwindcss({...sharedTailwind,content:["./src/**/*.{ts,tsx}","./setup/ui/**/*.{html,tsx}"]}),autoprefixer()]}},
  build:{target:"esnext",outDir:"setup/dist",rollupOptions:{input:fileURLToPath(new URL("./ui/index.html",import.meta.url))}},
});
