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

BlendShape の調整から衣装メニューの構築、Animator FX の編集まで、1.8.0 には実際に変更を適用するツールがあります。変更したいことを伝え、工程を確認しながらアバターを仕上げられます。

| 機能 | 実装済みの操作 |
| --- | --- |
| BlendShape と表情 | 顔・身体・衣装にある BlendShape の値を読み取り、変更してプレビューできます。顔立ちの調整には、そのための形状キーが必要です。 |
| 衣装・着せ替えメニュー | 衣装のバインド、排他的な衣装切替、衣装やアクセサリーのオン・オフ、VRChat の Expression Menu とパラメーターを編集できます。導入済みの Modular Avatar / VRCFury とも連携します。 |
| アニメーションと Animator FX | アニメーションカーブを作成・編集・一括処理し、FX のレイヤー、ステート、遷移を変更できます。表示切替やマテリアルの値を組み合わせ、着せ替えやディゾルブ演出を作成できます。 |
| マテリアル・Shader・テクスチャ | 色、数値、ベクトル、テクスチャ、マテリアルスロットを編集し、Shader を変更できます。lilToon と Poiyomi に限定せず、各 Shader が公開するプロパティを利用します。 |
| オブジェクト・ボーン・コンポーネント | オブジェクトの作成・複製・移動・親変更、コンポーネントの値の編集、Prefab の保存に対応。衣装のボーン統合、制約、対応する PhysBone コンポーネントも設定できます。 |
| 最適化とプロジェクトの確認 | VRAM、Mesh、マテリアル、パラメーター、ビルド状態を確認。テクスチャのサイズ・形式・圧縮を変更し、必要な依存関係がある場合は最適化コンポーネントを設定できます。 |
| 内蔵 AI と外部 MCP | Provider と API Key を設定して内蔵 Agent を使うか、外部 MCP Agent を接続します。どちらも読取り・計画・編集に対応し、選んだ権限モードに従って確認または自動実行します。 |
| Skills とワークフローの再利用 | 頭部交換やパーツ移植の内蔵ワークフロー、.vsk 技能パッケージの導入・書出し・有効化に対応。MCP Tools が操作、Resources が状態共有、Prompts が既存 Skills の再利用を担います。 |
| 見た目の確認・診断・復元 | Scene View の画像や Gesture Manager のパラメーター・状態を確認し、診断情報を読み取れます。チェックポイントを保存し、変更を確認して、別途承認のうえ復元できます。 |

表情・口形・フェイストラッキング用のキーが、そのまま顔立ち調整用になるわけではありません。Shader のプロパティと型、Modular Avatar・VRCFury・AAO などの依存関係を確認します。書込みは選択した権限に従い、チェックポイントの復元は別途確認します。

## Booth のアバターを始める手順

1. [v1.8.0 Release](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0) から
   Hotfix1 の Web Installer または Offline Installer と `VRCForge.unitypackage` を取得します。実行中のアプリを終了した後、「再試行」でインストールを続行できます。
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

書込みには逐次確認・自動・完全権限のモードがあります。自動モードでも高リスク操作は確認し、復元は常に別途確認します。チェックポイントは
バックアップの代わりではありません。Provider、依存 package、リグ、メニュー、FX、
Shader、見た目の結果はプロジェクトごとに確認されます。外部 MCP クライアントを使う場合も、
VRCForge を起動したままにし、クライアントに `vrcforge` Server と tools が表示されたことを確認してください。

衣装・表情・マテリアル・アニメーションは実際に編集できます。最適化連携には対応するプラグインが必要で、AAO の設定だけでビルド時の最適化が完了するわけではありません。頭部交換やパーツ移植でも、メッシュの継ぎ目・ウェイト・UV の編集が別途必要になる場合があります。

Avatar 保護コネクターは確認・計画・プレビューに対応します。公開パッケージには非公開の保護実行コンポーネントを含みません。

## リンク

- [v1.8.0 Release / Download](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.0)
- [Release Notes](https://github.com/ayyitong888/VRCForge/releases)
- [User Manual](USER_MANUAL.md)
- [English README](README.en.md) · [简体中文 README](README.md)
- [Unity Package guide](packaging/README.md)

## ライセンス

VRCForge は [GPL-3.0-only](LICENSE) で公開されています。公開 package に
第三者の Unity MCP ランタイムコードは含めていません。
