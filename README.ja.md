<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.8.1-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)

[简体中文](README.md) · [English](README.en.md) · **日本語**

🌙 **VRCForge Atelier** · VRChat アバター改変を、楽しく確認しながら ✨

**[🌸 創作工房の公式サイト](https://ayyitong888.github.io/VRCForge/ja/)**

</div>

# VRCForge：VRChat アバター改変を支援する AI 工房

VRCForge は、Booth などでアバターや衣装を選ぶクリエイター向けの、Windows 用
オープンソース **VRChat avatar editor** です。変更したい顔立ち、衣装、マテリアル、
アニメーションを自然な言葉で伝えると、アバターを確認し、変更を提案して適用できます。

衣装の着せ替え、マテリアルやアニメーションの編集、頭部やパーツの移植まで、
変更する対象を画面で確認し、適用後の結果を確かめながら作業できます。変更ごとに確認するか、
条件に合う変更を自動で進めるかを選べます。復元には、必ず確認が必要です。
対応状況はアバター、導入済みプラグイン、Unity プロジェクトによって異なるため、
自分のプロジェクトで結果を確認してください。

> Unity / VRChat Avatar プロジェクトを書き換える機能を使う前に、必ずバックアップしてください。

**[VRCForge v1.8.1 をダウンロード](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.1)**

## できること

細かな調整から全身のコーディネートまで。繰り返しの操作は AI に任せて、創作を楽しむ時間を増やしましょう。

| やりたいこと | VRCForge でできること |
| --- | --- |
| 見た目と表情を調整 | アバターに用意された顔立ち・体形・表情の調整項目を使い、見た目を確認しながら仕上げられます。 |
| 衣装とアクセサリーを追加 | アバターに衣装やアクセサリーを合わせ、ゲーム内で使う着せ替えメニューを整理できます。 |
| 着せ替えをアニメーションに | 衣装の切り替えにアニメーションを付け、フェードやディゾルブなどの演出を作れます。 |
| 色と質感をそろえる | 衣装・髪・アクセサリーの色やテクスチャ、マテリアルを調整して、好みのコーデに近づけられます。 |
| モデルのパーツを組み合わせる | 頭部交換やパーツ移植のワークフローに沿って部品を組み合わせ、つながりを確認できます。 |
| アバターを軽くする | 負荷の大きいテクスチャや部品を確認し、画像サイズや圧縮を調整。導入済みのプラグインを使った最適化にも対応します。 |
| 使いたい AI を選ぶ | API Key を設定して内蔵 AI を使うか、MCP で普段の AI アシスタントを接続。操作を一つずつ確認するか、自動で進めるかを選べます。 |
| いつもの手順を再利用 | よく使う操作を Skills として保存し、次の作業にも活用。.vsk 技能パッケージの読み込み・書き出しもできます。 |
| 仕上がりを確認・復元 | 変更後の見た目やアニメーションを確認し、途中の状態を保存できます。戻したいときは保存した状態を選び、確認して復元します。 |

調整できる内容はアバターによって異なります。顔立ちの変更にはモデル側の調整項目が必要です。一部の着せ替え・最適化機能には追加プラグインを使います。

## Booth のアバターを始める手順

1. [v1.8.1 Release](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.1) から
   修正済みの Web Installer または Offline Installer と `VRCForge.unitypackage` を取得します。実行中のアプリを終了した後、「再試行」でインストールを続行できます。
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

- [v1.8.1 Release / Download](https://github.com/ayyitong888/VRCForge/releases/tag/v1.8.1)
- [Release Notes](https://github.com/ayyitong888/VRCForge/releases)
- [User Manual](USER_MANUAL.md)
- [English README](README.en.md) · [简体中文 README](README.md)
- [Unity Package guide](packaging/README.md)

## ライセンス

VRCForge は [GPL-3.0-only](LICENSE) で公開されています。公開 package に
第三者の Unity MCP ランタイムコードは含めていません。
