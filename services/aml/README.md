# AML image delivery

`/амл` uses the existing single-worker queue. The report is rendered as a dark
A4 vector PDF by `AMLReportPDFBuilder`, off the event loop via the shared
`SerializedThreadExecutor` implementation. Long source lists continue on further
pages; TRC20 wallet balances and TRC20 transaction hashes are included when present.
Telegram receives only PNG report pages, not the intermediate PDF. The first photo
caption includes the full source breakdown and the checked wallet's USDT balance,
without a GetBlock URL or PDF notice. Statistics exceeding the caption limit
continue losslessly in plain-text messages. The same Tronscan balance
is passed to the PDF's "Остаток USDT" field, including a zero balance. If Tronscan
is unavailable, both formats say so rather than inventing a zero balance.

The process does not write PDF files or keep a PDF cache. `BytesIO` is closed on
success and failure. `BufferedInputFile.data` is cleared in `finally` after the
Telegram upload, including cancellation. Only static fonts are registered globally.
On cancellation the thread adapter waits for an in-progress renderer to finish;
there are no detached render tasks or per-request thread pools.

The queue drops its last result and callbacks before waiting for another request.
Shutdown cancels the current job and discards pending callbacks; failure of a
Telegram error notification does not kill the worker. The global bot HTTP session
continues to be owned and closed by the application's existing lifecycle.

Paid checks are charged once, using the existing ledger idempotency key, after all
images and statistics have been delivered. Rendering or upload failures are not
charged. The temporary queue notification is deleted on success. Telegram is the
persistent image store; the message archive stores generated photo metadata/file
IDs only (`download_status=skipped`) and does not download the images.

Intermediate PDF pages are rendered with PDFium and sent as Telegram photos.
Only one PNG is generated/uploaded at a time; PDFium document, page, bitmap, PIL
image and BytesIO are explicitly closed before returning the PNG bytes. A global
lock serializes native PDFium operations across all executor instances. On upload
success/error/cancellation, the ephemeral upload releases its bytes and streams.
Generated outbound AML photos are metadata-only in the message archive as well;
regular incoming/outgoing photos keep their existing archival behavior.

Install the updated `requirements.txt` when deploying (`reportlab` is new).
No database migration is needed. `pypdf` is a dev-only dependency for validating
generated pages, full text, and links. Fonts are bundled for Cyrillic support on
servers without desktop fonts; their license is in `assets/DejaVu-LICENSE.txt`.
