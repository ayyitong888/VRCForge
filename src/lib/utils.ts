import { clsx, type ClassValue } from "clsx";
import { twMerge } from "tailwind-merge";

export function cn(...inputs: ClassValue[]) {
  return twMerge(clsx(inputs));
}

export function formatCount(value: number | undefined) {
  return typeof value === "number" ? value.toLocaleString("zh-CN") : "0";
}
