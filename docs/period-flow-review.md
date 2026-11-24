## Review 結論

之前嘅方向「喺 Period Flow 選月份、確認後執行 Step 1」合理，但我會**修正兩點**：

1. **唔需要先持久化建立月份。** 之前我話要另外保存新月份並唔必要；目前月份列由 runs 推導，Step 1 成功加入佇列後，該月份自然會出現。只有業務要求「未執行都要保存空白月份」先需要新資料表。
2. **唔應假設 Step 1 成功就可以簡單順序解鎖所有後續步驟。** 現有 Step 3 有例外來源、Step 4 和 Step 6 有各自確認及準備要求，Step 5 未配置；要保留現有規則。

## 建議未來操作流程

以 **October 2026** 為例：

1. Period Flow 頂部提供 **Start a month** 入口：選年份、月份及輸入 operator。
2. 顯示確認畫面：**October 2026 · Step 1 Check submission**，清楚說明確認後會加入佇列，而非直接完成。
3. 按確認時重新檢查條件；成功排隊後顯示 10 月列及 **Queued**。如果排隊失敗，顯示原因，不建立假紀錄。
4. 列內使用 **Actions** 選擇具體步驟，而唔係含糊嘅 `Run`；保留 **View details** 查看來源 run、錯誤及 logs。
5. `Run All` 如提供，必須明確標示**只執行 Steps 1–3**；Steps 4、6 仍須獨立操作，Step 5 保持未配置。

這個方案保留之前「唔加 Create period 按鈕」嘅方向：入口係**開始執行**，唔係先建立一個空月份。

## 實作前要處理嘅重點

### 高優先：執行安全

- **月份必須綁定操作本身。** 現有 `queue_run()` 從共用 session state 讀月份；未來若用某一列嘅按鈕，應將該列月份明確帶到確認及排隊流程，避免選咗 10 月卻用到另一個月份。[app.py](web_app_v2/app.py#L194)
- **確認後重新檢查狀態。** 頁面可能因自動刷新、另一位使用者或排程而過時。資料庫嘅「檢查已有 active run」和「新增 run」需要作為原子操作處理；現時程式係先查詢再插入，現有測試只驗證順序操作，未證明並發安全。[repository.py](pipeline_core/repository.py#L164)、[test_worker.py](tests/test_worker.py#L35)
- **排隊狀態唔好顯示成 Not started。** 現時矩陣按已記錄嘅 step result 顯示；run 已 `QUEUED`、但 worker 未建立 step record 時，仍可能顯示 `Not started`。應額外顯示相關 run 的 `Queued`／`Running`，並區分歷史結果，唔好當作已執行結果。[view_models.py](web_app_v2/view_models.py#L192)
- **重跑要保留 audit，並按步驟確認副作用。** 特別係 Step 3 上傳及 Step 4 Snowflake task，失敗或 timeout 唔一定代表外部完全冇執行，不能自動重試。現有重複檢查以 run 的 `target` 判斷；`ALL` 同單獨 Step 1 並非相同 target，所以要先釐清及測試跨 `ALL`／單步的重跑政策，不能只依賴現有檢查。[repository.py](pipeline_core/repository.py#L170)

### 高優先：保留現有步驟規則

| 動作 | 未來 Period Flow 應保留 |
|---|---|
| Step 2 | 同月 Step 1 成功，以及現有 project 選擇流程。 |
| Step 3 | 預覽 Step 2 檔案；保留選擇例外資料夾的流程及審計。 |
| Step 4 | Stage 檔案確認、本機 readiness、worker 狀態；不可當作已驗證 Snowflake 連線。 |
| Step 5 | 顯示未配置，不提供執行。 |
| Step 6 | 確認 Step 3 來源及 Risk workbook，並保留執行時再次驗證。 |

另外，現有 V2 Step 3 對話框引用咗 `repo`，但該檔案未見其定義；若要重用，應先修正並做實際互動測試，不能假設目前流程已可靠。[app.py](web_app_v2/app.py#L356)

### 中優先：月份及狀態語意

- 目前 `resolve_reporting_month()` 係**建議／提示邏輯**，唔等於後端強制月份先後次序。是否禁止 9 月未完成 Step 6 就開始 10 月，需要業務決定；未決定前應顯示警告，唔好聲稱系統已強制禁止。[view_models.py](web_app_v2/view_models.py#L30)
- 矩陣顯示最新 step result，但 worker 尋找 prerequisite 時使用最新**成功 run**，而且要求 parent run 成功。部分完成後失敗嘅 `Run All` 可能令畫面狀態與「可否執行下一步」不同；介面應顯示實際阻擋原因，並先釐清是否要改既有規則。[repository.py](pipeline_core/repository.py#L189)、[worker_main.py](worker/worker_main.py#L154)

## 建議交付次序

1. **先做 Step 1 新月份入口**：確認、排隊、狀態及錯誤；不新增空月份儲存。
2. 補齊並發、重跑、錯月份及排程競爭測試，再逐步接入 Steps 2–6，重用現有確認與後端驗證。
3. 用臨時資料庫及 mock worker 做行為測試，另外做瀏覽器操作驗證；唔使用正式資料排隊測試。

以上係設計 review；**目前 Period Flow 仍然唯讀**，未作任何改動。