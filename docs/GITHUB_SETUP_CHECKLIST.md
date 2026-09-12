# GitHub Repository Setup Checklist

Use this checklist for the public repository at
<https://github.com/ayyitong888/VRCForge>. These settings are not changed by a
Git commit; a repository administrator must apply them in GitHub.

## About panel

Open the repository page, select the gear icon beside **About**, and copy the
following values.

### Description

```text
AI-assisted VRChat avatar editor, Unity MCP server and local AI agent. Tools for avatar customization, outfits, wardrobes, BlendShapes, lilToon materials and optimization checks, with approval, checkpoints and restore. VRChat 改模工具：捏脸、换装、衣柜、材质与优化诊断。VRChat アバター改変・着せ替え・表情調整支援。
```

### Website

Recommended release destination:

```text
https://github.com/ayyitong888/VRCForge/releases/latest
```

Documentation alternative:

```text
https://github.com/ayyitong888/VRCForge/blob/main/USER_MANUAL.md
```

Use one value in the GitHub **Website** field. Prefer the Releases URL for an
installer-first public page; use the manual URL when documentation discovery is
the higher priority.

### Topics

Copy these topics individually:

```text
vrchat
vrc
vrchat-avatar
avatar-editing
avatar-tools
ai-agent
mcp
model-context-protocol
ai-workbench
local-first
unity
unity-editor
tauri
fastapi
windows
avatar-editor
avatar-customization
vrchat-avatars
vrchat-tool
unity-mcp
```

Also enable **Releases** in the About panel. Enable **Packages** or
**Deployments** only if the repository actually publishes those resources.

## Check repository search discovery

GitHub's default repository search matches the name, description, and topics.
README content is included only when a query uses `in:readme`. Keep the About
description readable and specific about supported tasks; use relevant category
topics rather than unrelated keywords or other projects' names. GitHub allows
up to 20 topics, so the list above preserves the existing topics and adds five
avatar and Unity MCP categories.

After applying the settings, test unqualified searches such as
`vrchat avatar editor`, `vrchat avatar tools`, `unity mcp`, `vrchat 改模`,
`vrchat 换装`, and `vrchat アバター改変`. Record the date, result limit, and
whether the repository appears. An exact-name or `repo:` search proves only
that the repository is indexed; it does not prove discovery from a task term.
Search ranking can change, and a saved About field does not itself prove
that the search index has refreshed.

References: [Searching for repositories](https://docs.github.com/en/search-github/searching-on-github/searching-for-repositories)
and [Classifying your repository with topics](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/classifying-your-repository-with-topics).

## Social preview

The canonical banner source is
[`docs/assets/social-preview.svg`](assets/social-preview.svg). It is already
1280 × 640 with a solid background, product name, tagline, and version badge.

GitHub accepts PNG, JPG, or GIF social-preview uploads rather than SVG. Use the
SVG as the source of truth and export a local `1280 × 640` PNG under `1 MB`:

1. Open `docs/assets/social-preview.svg` in a vector editor or browser.
2. Export or render it to a 1280 × 640 PNG. Confirm that the title, tagline,
   version, and safe margins are intact.
3. On the repository page, open **Settings**. If the tab is hidden, use the
   repository navigation dropdown and choose **Settings**.
4. Under **Social preview**, select **Edit** → **Upload an image...**.
5. Upload the rendered PNG and save the change.
6. Open the public repository in a signed-out/private window and inspect a
   shared repository link after caches refresh.

Do not upload a placeholder screenshot or an unreviewed image containing local
paths, project names, avatars, paid assets, API keys, or other private data.

## Final public-page review

- [ ] Description and website render without truncating essential meaning.
- [ ] All recommended topics appear and link to the expected GitHub topic pages.
- [ ] The social preview is sharp at both wide and small-card sizes.
- [ ] README badges and internal documentation links resolve on the default
      branch.
- [ ] The latest Release link points to the intended stable release.
- [ ] License, security policy, contributing guide, code of conduct, and issue
      templates appear in GitHub's community profile checks.
- [ ] No local-only status, roadmap, evidence, credentials, session data,
      machine paths, or private-addon details are exposed.

Reference: [GitHub Docs — Customizing your repository's social media
preview](https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/customizing-your-repository/customizing-your-repositorys-social-media-preview).
