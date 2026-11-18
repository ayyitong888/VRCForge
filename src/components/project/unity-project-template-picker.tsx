import { useId } from "react";
import { useTranslation } from "react-i18next";
import type { UnityProjectTemplate } from "../../lib/api";

const knownFamilies = ["avatar", "world", "base"];
const versionOrder = new Intl.Collator("en", { numeric: true });

function templateFamily(template: UnityProjectTemplate): string {
  const name = template.name.trim().toLowerCase();
  const known = /^(avatar|world|base)(?: (\d{4}))?$/.exec(name);
  if (known && (!known[2] || template.unityVersion?.startsWith(`${known[2]}.`))) return known[1];
  return name;
}

function compareVersions(first: UnityProjectTemplate, second: UnityProjectTemplate): number {
  const a = first.unityVersion || "";
  const b = second.unityVersion || "";
  return Number(/^\d/.test(b)) - Number(/^\d/.test(a)) || versionOrder.compare(b, a);
}

export function defaultUnityProjectTemplate(templates: UnityProjectTemplate[]): UnityProjectTemplate | undefined {
  const avatars = templates.filter((template) => templateFamily(template) === "avatar");
  return (avatars.length ? avatars : [...templates]).sort(compareVersions)[0];
}

export function UnityProjectTemplatePicker({ templates, selected, disabled, onSelect }: {
  templates: UnityProjectTemplate[];
  selected: UnityProjectTemplate;
  disabled: boolean;
  onSelect: (templatePath: string) => void;
}) {
  const { t } = useTranslation();
  const group = useId();
  const families = new Map<string, UnityProjectTemplate[]>();
  for (const template of templates) {
    const family = templateFamily(template);
    const options = families.get(family);
    if (options) options.push(template);
    else families.set(family, [template]);
  }
  for (const options of families.values()) options.sort(compareVersions);
  const selectedFamily = templateFamily(selected);
  const familyOrder = [...families].sort(([a], [b]) => {
    const index = (family: string) => knownFamilies.includes(family) ? knownFamilies.indexOf(family) : knownFamilies.length;
    return index(a) - index(b);
  });
  const versions = families.get(selectedFamily) ?? [];
  const versionLabel = (template: UnityProjectTemplate) => !template.unityVersion || template.unityVersion === "Unknown"
    ? t("project.creation.unknownUnityVersion")
    : `Unity ${template.unityVersion}`;

  return (
    <fieldset className="min-w-0 space-y-2" disabled={disabled}>
      <legend className="mb-1.5 text-sm font-medium">{t("project.creation.template")}</legend>
      <div className="flex flex-wrap gap-2">
        {familyOrder.map(([family, [defaultTemplate]]) => {
          const label = knownFamilies.includes(family)
            ? t(`project.creation.family.${family}`)
            : defaultTemplate.name;
          return (
            <label key={family} className="min-w-0 max-w-full">
              <input
                type="radio"
                name={group}
                className="peer sr-only"
                checked={family === selectedFamily}
                onChange={() => onSelect(defaultTemplate.path)}
              />
              <span className="block min-w-20 cursor-pointer truncate rounded-md border border-border bg-background px-3 py-2 text-center text-sm font-medium peer-checked:border-primary peer-checked:bg-primary/10 peer-checked:text-primary peer-focus-visible:ring-2 peer-focus-visible:ring-primary peer-disabled:cursor-default peer-disabled:opacity-60">
                {label}
              </span>
            </label>
          );
        })}
      </div>
      {versions.length > 1 ? (
        <select
          aria-label={t("project.creation.unityVersion")}
          value={selected.path}
          className="max-w-full rounded-md border border-border bg-background px-2 py-1.5 text-xs text-foreground focus-visible:outline-primary"
          onChange={(event) => onSelect(event.target.value)}
        >
          {versions.map((template) => {
            const sameVersion = versions.filter((item) => item.unityVersion === template.unityVersion);
            const sameSource = sameVersion.filter((item) => item.source === template.source);
            const source = sameVersion.length > 1 ? t(`project.creation.source.${template.source}`) : "";
            const ordinal = sameSource.length > 1 ? ` ${sameSource.indexOf(template) + 1}` : "";
            return <option key={template.path} value={template.path}>
              {source ? `${versionLabel(template)} · ${source}${ordinal}` : versionLabel(template)}
            </option>;
          })}
        </select>
      ) : <p className="text-xs text-muted-foreground">{versionLabel(selected)}</p>}
      <details key={selected.path} className="text-xs text-muted-foreground">
        <summary className="w-fit cursor-pointer focus-visible:outline-primary">{t("project.creation.templateDetails")}</summary>
        <p className="mt-1 select-text break-all">{selected.path}</p>
      </details>
    </fieldset>
  );
}
