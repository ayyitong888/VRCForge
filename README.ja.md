<div align="center">

![VRCForge — AI Agent + MCP for VRChat Avatar Editing](docs/assets/vrcforge-atelier-banner.png)

[![Stable](https://img.shields.io/github/v/release/ayyitong888/VRCForge?label=stable&style=flat-square)](https://github.com/ayyitong888/VRCForge/releases/latest)
![Current version](https://img.shields.io/badge/current-v1.10.5-d9487c?style=flat-square)
[![License GPL-3.0-only](https://img.shields.io/badge/license-GPL--3.0--only-64748b?style=flat-square)](LICENSE)
![Windows x64](https://img.shields.io/badge/platform-Windows%20x64-0ea5e9?style=flat-square)

[简体中文](README.md) · [English](README.en.md) · **日本語**


**[公式サイト](https://ayyitong888.github.io/VRCForge/ja/)**

</div>

# VRCForge：VRChat アバター改変を支援する AI 工房

VRCForge は、Booth などでアバターや衣装を選ぶクリエイター向けの、Windows 用
オープンソースのアバター編集ツール です。変更したい顔立ち、衣装、マテリアル、
アニメーションを自然な言葉で伝えると、アバターを確認し、変更を提案して適用できます。

> Unity / VRChat Avatar プロジェクトを書き換える機能を使う前に、必ずバックアップしてください。

現在の正式版：[1.10.5](https://github.com/ayyitong888/VRCForge/releases/tag/v1.10.5) · [更新内容](docs/RELEASE_NOTES_1.10.5.md)

## ダウンロード：App と Unity プラグインの両方が必要

**同じ Release から、以下の両方をダウンロードしてください。**

| 必須ダウンロード | インストール先 |
| --- | --- |
| **[App オフラインインストーラー（.exe）](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.5/VRCForge_Offline_Installer_x64.exe)** | Windows で実行。[Web インストーラー](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.5/VRCForge_Web_Installer_x64.exe) でも可。どちらか一つを選びます。 |
| **[Unity Core プラグイン（VRCForge.unitypackage）](https://github.com/ayyitong888/VRCForge/releases/download/v1.10.5/VRCForge.unitypackage)** | 接続する各 Unity プロジェクトに **Import All**。App のインストールだけでは Unity に接続できません。 |

アップグレード時も App と Unity プラグインの両方を更新してください。

## はじめて使う

1. **プロジェクトをバックアップし、App のインストーラーを実行します。** 画面の案内に従ってインストールしてください。
2. **Unity プロジェクトを開き、プラグインを導入します。** Unity の `Assets > Import Package > Custom Package…` からダウンロードした `VRCForge.unitypackage` を開き、**Import All** をクリックします。VRChat Avatar SDK が不足している場合は案内に従って追加し、コンパイルが終わるまで待ちます。
3. **VRCForge を開き、そのプロジェクトと Avatar を選びます。** Unity は開いたままにし、App で接続済みになっていることを確認します。一覧にない場合は「新規プロジェクト」から、`Assets` と `ProjectSettings` が入っているフォルダーを追加してください。
4. **以下から AI の使い方を選びます。** 接続できたら、まず「このアバターを確認して。まだ変更しないで」と頼んでみてください。

Avatar プロジェクトがまだない場合は、[Creator Companion（VCC）](https://vcc.docs.vrchat.com/guides/getting-started/) や ALCOM で作成できます。接続できないときは、Unity が開いているか、プラグインを導入したか、Console に赤いコンパイルエラーがないかを確認してください。

## AI を選ぶ

- **VRCForge でチャットする：** 「設定 → モデル」でモデルサービスを選び、API キーを入力して保存します。モデルを選んで接続をテストしてください。
- **手持ちの MCP エージェントを使う：** 「設定 → コネクター」で「外部 Agent に接続」をオンにし、クライアントを選んで「MCP 設定を追加」をクリックします。汎用接続ガイドをコピーしてエージェントに渡すこともできます。VRCForge と Unity を開いたままクライアントを再起動・再接続し、アバターの確認を依頼してください。編集するときは「プロジェクトの変更を許可」をオンにします。[詳しい接続手順](USER_MANUAL.md#external-agent-connectors)

## 編集・復元・手順の再利用

編集前に対象と計画を確認し、編集後に見た目と結果を確認してください。操作ごとの確認、自動、完全権限のモードを選べます。自動モードでも高リスク操作は確認し、**チェックポイントの復元には常に別途確認が必要です**。チェックポイントはプロジェクトのバックアップの代わりにはなりません。

よく使う手順は **Skills** として保存でき、**.vsk スキルパッケージ** の読み込み・書き出しもできます。[スキルの使い方](USER_MANUAL.md#skill-packages-and-path-to-skill)

動作環境は Windows x64、Unity 2022.3 LTS、VRChat SDK3 Avatar プロジェクトです。編集できる範囲はアバターの調整項目と導入済みプラグインによって異なります。頭部交換やパーツ移植には、メッシュ・ウェイト・UV の手作業が必要な場合があります。対応範囲と制限は [ユーザーマニュアル](USER_MANUAL.md) と [互換性一覧](docs/COMPATIBILITY_MATRIX.md) を参照してください。

## ドキュメントと開発

- [ユーザーマニュアル](USER_MANUAL.md)：操作手順、診断、コマンドライン、トラブルシューティング
- [最適化](docs/OPTIMIZATION_STRATEGY.md) · [Unity パッケージ作成](packaging/README.md) · [回帰検証の要件](docs/PRODUCT_REGRESSION_CONTRACT.md)
- [貢献ガイド](CONTRIBUTING.md) · [行動規範](CODE_OF_CONDUCT.md) · [セキュリティ報告](SECURITY.md)

通常は上記のインストーラーをご利用ください。ソースから開発する場合は、リポジトリのルートで実行します。

```powershell
python -m pip install -r requirements.txt
start_dashboard.cmd
```

## プライバシーとライセンス

プロジェクト索引、チャット、メモリ、チェックポイントは既定でローカルに保存されます。外部モデルサービスに送る内容はモデルと操作によって異なります。キー、有料アセット、非公開ファイルは公開せず、診断データを共有する前に内容を確認してください。[プライバシーの詳細](USER_MANUAL.md#privacy-boundary)

VRCForge は [GPL-3.0-only](LICENSE) で公開されています。Unity MCP Core とツール契約はプロジェクト独自の実装で、公開パッケージに第三者の Unity MCP ランタイムコードは含めていません。第三者の依存関係と表記は [DEPENDENCIES](DEPENDENCIES.md) と [NOTICE](NOTICE) を参照してください。
