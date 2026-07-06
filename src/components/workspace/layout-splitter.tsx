import type { PointerEvent as ReactPointerEvent } from "react";

type LayoutSplitterProps = {
  side: "left" | "right";
  value: number;
  min: number;
  max: number;
  title: string;
  onPointerDown: (event: ReactPointerEvent<HTMLDivElement>) => void;
};

export function LayoutSplitter({ side, value, min, max, title, onPointerDown }: LayoutSplitterProps) {
  return (
    <div
      role="separator"
      aria-orientation="vertical"
      aria-valuenow={Math.round(value)}
      aria-valuemin={min}
      aria-valuemax={max}
      data-layout-splitter={side}
      className="group relative h-screen cursor-col-resize touch-none bg-transparent"
      onPointerDown={onPointerDown}
      title={title}
    >
      <div className="absolute inset-y-0 left-1/2 w-px -translate-x-1/2 bg-border/80 transition-colors group-hover:bg-primary group-active:bg-primary" />
    </div>
  );
}
