# GENESIS source records

The adoption capture establishes **`intevia-root-lo`** as the canonical logical identity for INTEVIA. Its correction supersedes the proposal’s spelling `intevia-originating-lo`. GENESIS checks the exact bytes of these three records.

| File | Purpose | SHA-256 |
|---|---|---|
| `PRINCIPAL_INVOCATION_RECEIVED_v1.txt` | Original authority-principal invocation for GENESIS. | `57a247b376dfff11834ba8521ed66589a02b2cbf3c45e04bfc6f3738ea7367d7` |
| `ROOT_DESIGNATION_INPUT_v1_0.md` | Designation proposal. Its original text is retained for byte verification. | `d1fbe0010f4d52e7fa562ad70000fc2076cb33b67336812ff718fb930d3ee831` |
| `HG_RULINGS_R_P3_RD1_RD2_RD3_20261105_CAPTURE.txt` | Human Governor’s adoption and correction, captured by the Vision Chamber. | `3544c14ac5f2ec8083b6bad2666afb062a907f73e578aaf84ada37e929f3beb9` |

The folder’s `.gitattributes` sets `-text` on each of the three pinned records. Git therefore preserves their exact bytes during staging and checkout, including the principal invocation’s CRLF line endings and the other two records’ LF line endings.
