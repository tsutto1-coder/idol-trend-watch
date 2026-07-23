# IDOL TREND WATCH

「いま伸びているアイドルコンテンツを毎朝届ける速報」

毎朝7:00に、YouTube上で話題のアイドル公式コンテンツ(MV・新曲・ダンスプラクティス等)を自動収集・AI分析し、**女性アイドルTOP10/男性アイドルTOP10**の2本立てランキングと、SNS投稿文・縦型動画を生成するツールです。CM TREND WATCHと同じ基盤で動きます。

## 重要な設計方針

- **人物の人気投票ではなく、公式コンテンツの話題度ランキング**です(音楽チャートと同じ立ち位置)
- AIの分析コメントは楽曲・映像・企画についてのみ。**メンバー個人の外見・私生活への言及は禁止**(プロンプトで制御)
- 画像・動画・サムネイルは一切保存せず、公式チャンネルへのリンクのみ掲載
- K-POPを含む設定がデフォルト(`CONFIG["include_kpop"]`)。検索語・件数などは `idol_trend_watch.py` 冒頭のCONFIGで調整可

## 毎朝の生成物(outputs/日付/)

- digest.txt … 確認用サマリー(男女別TOP10+連続再生リンク)
- x.txt / threads.txt / instagram.txt / note.md … 男女2部構成の投稿文
- female_reel.mp4 / female_feed.mp4 … 女性ランキング動画(9:16 / 4:5)
- male_reel.mp4 / male_feed.mp4 … 男性ランキング動画
- 発表順はすべて 3位→2位→1位→4〜10位一覧。背景色は季節で自動変化

## セットアップ

CM TREND WATCHと同一手順です。新しいGitHubリポジトリ(例: idol-trend-watch)を作成し、このフォルダの中身をアップロード。Secretsは**CM版と同じ値を再利用できます**:

- YOUTUBE_API_KEY / ANTHROPIC_API_KEY(共通でOK)
- GDRIVE_CLIENT_ID / GDRIVE_CLIENT_SECRET / GDRIVE_REFRESH_TOKEN(共通でOK。Driveには「IDOL TREND WATCH」フォルダが別に作られます)
- DISCORD_WEBHOOK_URL(任意)

動作確認: `python idol_trend_watch.py --demo` → `python make_video.py`(架空グループのデータで出力されます)

## 運用上の注意

- アイドルか一般アーティストかの線引きはCMより曖昧なため、**AI判定の誤りはCM版より起きやすい**前提で、投稿前のdigest.txtチェックを必ず行ってください(切り抜き・ファンカムの混入にも注意)
- YouTube APIの無料枠(1日10,000ユニット)はCM版と合算されます。2つ合わせても1日約1,600ユニットなので余裕があります
- Claude API費用は候補35件分析のため、CM版よりわずかに増えます(それでも月数百円規模)
