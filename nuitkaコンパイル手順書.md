# nuitkaコンパイル手順書

## 概要

このドキュメントは、PlanetSide 2 日本語化MOD UIツールをnuitkaを使ってexe化するための手順書です

## 前提条件

*   Pythonがインストールされていること
*   nuitkaがインストールされていること
    *   `pip install nuitka`でインストールできる
*   必要なPythonパッケージがインストールされていること
    *   `pip install -r requirements.txt`でインストールできる

## 手順


1.  **コンパイルコマンドの実行:**
    *   ターミナルで以下のコマンドを実行してください。

        ```
        python -m nuitka --onefile --onefile-as-archive --windows-console-mode=disable --enable-plugin=pyside6 --windows-icon-from-ico=resources/ps2jpmod.ico --include-data-files=resources/ps2jpmod.ico=resources/ps2jpmod.ico --output-dir=%OUTPUT_DIR% --output-filename=PS2JPMod_unsigned --clean-cache=all --remove-output main.py
        ```

        ```
        python -m nuitka --onefile --onefile-as-archive --windows-console-mode=force --enable-plugin=pyside6 --windows-icon-from-ico=resources/ps2jpmod.ico --include-data-files=resources/ps2jpmod.ico=resources/ps2jpmod.ico --output-dir=%OUTPUT_DIR% --output-filename=PS2JPMod_unsigned --clean-cache=all --remove-output main.py
        ```

        *   `--standalone`: 依存関係を全部含める
        *   `--onefile`: 一つのファイルにまとめる
        *   `--windows-console-mode=disable`: コンソールウィンドウを非表示にする
        *   `--enable-plugin=pyside6`: PySide6プラグインを使う
        *   `--windows-icon-from-ico`: アイコンを指定する
        *   `--include-data-files`: `src/resources/icon.ico`を`resources/icon.ico`として含める
        *   `--include-data-dir=data=data`
        *   `--output-filename`: 出力ファイル名を指定する
2.  **exeファイルの確認:**
    *   `main.dist`フォルダの中に`PS2JPMod.exe`が生成されていることを確認してください。
    *   `PS2JPMod.exe`を実行して、ちゃんと動くか確認してください。

## トラブルシューティング