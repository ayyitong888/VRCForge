<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.8.0-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)

[简体中文](README.md) · [English](README.en.md) · **日本語**

🌙 **VRCForge Atelier** · VRChat アバター改変を、楽しく確認しながら ✨

**[🌸 創作工房の公式サイト](https://ayyitong888.github.io/VRCForge/ja/)**

</div>

# VRCForge：VRChat アバター改変を支援する AI 工房

VRCForge は、Booth でアバターや衣装を選ぶクリエイター向けの、Windows 用
オープンソース **VRChat avatar editor** です。AI Agent、Unity Editor、
**Unity MCP** をつなぎ、確認 → 相談 → プレビュー → 承認 → 適用 → 検証という
流れで作業を進めます。コードを書かなくても、作業の対象と結果を画面で確認できます。

**[VRCForge v1.8.0 をダウンロード](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)**

## できること

- **アバター改変**：顔、表情、衣装、マテリアル、性能チェックを AI と一緒に整理できます。
- **表情 BlendShape**：利用できる形状キーを確認し、小さな変更をプレビューしてから判断できます。
- **衣装着せ替え**：衣装と衣装メニューの確認、導入・バインド案の作成を支援します。書き込みを伴う衣装ワークフローは Beta です。
- **マテリアルと shader optimization**：lilToon、Poiyomi、その他の対応可能な Shader を調べ、利用できるプロパティに沿って最適化案を作ります。特定ベンダーだけを前提にはしません。
- **Unity MCP**：外部 Agent は読み取りと計画を行い、Unity の変更は VRCForge の承認フローを通ります。

対応状況はアバター、Unity プロジェクト、SDK、衣装の構成によって変わります。
顔の BlendShape（形状キー）が存在しないアバターに、機械的に「顔を作れる」とは約束しません。
書き込み前後に対象、プレビュー、読戻し結果を確認してください。

## Booth のアバターを始める手順

1. [v1.8.0 Release](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0) から
   Web Installer または Offline Installer と `VRCForge.unitypackage` を取得します。
2. Unity 2022.3 LTS の VRChat SDK3 Avatar プロジェクトをバックアップし、
   package を Import All します。コンパイル完了後、`[VRCForge MCP] Core Ready` を確認します。
3. VRCForge を起動し、対象プロジェクトと Avatar を選びます。最初は Doctor、
   読み取りスキャン、Validation Report の順に実行してください。
4. 顔の表情や衣装など小さな依頼を出し、対象と計画を確認してから承認します。
5. 適用後はチェックポイント、読戻し、検証差分を確認します。必要な場合だけ、
   独立した復元操作を承認します。

Unity package の導入や最初の接続で迷った場合は、[English README](README.en.md) の
Quick start と [User Manual](USER_MANUAL.md) を参照してください。

## 安全な作業の考え方

通常モードの Unity アセット書き込みには明示的な承認が必要です。チェックポイントは
バックアップの代わりではありません。Provider、依存 package、リグ、メニュー、FX、
Shader、見た目の結果はプロジェクトごとに確認されます。外部 MCP クライアントを使う場合も、
VRCForge を起動したままにし、クライアントに `vrcforge` Server と tools が表示されたことを確認してください。

衣装着せ替え、表情 BlendShape、マテリアル調整、Avatar optimization の相談はできますが、
Beta と表示された機能やプロジェクト固有の作業は、実際の Unity プロジェクトで検証してから採用してください。

## リンク

- [v1.8.0 Release / Download](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)
- [Release Notes](https://github.com/ayyitong888/VRCForge/releases)
- [User Manual](USER_MANUAL.md)
- [English README](README.en.md) · [简体中文 README](README.md)
- [Unity Package guide](packaging/README.md)

## ライセンス

VRCForge は [GPL-3.0-only](LICENSE) で公開されています。公開 package に
第三者の Unity MCP ランタイムコードは含めていません。
