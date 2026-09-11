import streamlit as st
import fitz  # PyMuPDF
import re
from decimal import Decimal, ROUND_HALF_UP
import io

st.set_page_config(page_title="THB to USD Quotation Converter", layout="centered")

st.title("📄 THB ➔ USD 見積書変換ツール")
st.write("PDFのタイバーツ見積書をアップロードし、為替レートを入力してUSDへ変換します。")

# レート入力（初期値は空欄）
rate_input = st.text_input("為替レート (1 USD = ? THB)", value="")

# PDFファイルアップローダー
uploaded_file = st.file_uploader("変換するPDFファイルを選択またはドラッグ＆ドロップしてください", type=["pdf"])

def process_pdf_bytes(pdf_bytes, rate_text):
    rate = Decimal(rate_text)
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    
    total_u1_acc = 0
    total_u3_acc = 0
    accumulated_discount_usd = 0
    x_walls = {"u1_amt": 0, "u3_amt": 0, "total": 0}

    for page in doc:
        # 1. テキスト書き換え (Baht -> USD)
        matches = page.search_for("(Baht)")
        for rect in matches:
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((rect.x0, rect.y1 - 2), "(USD)", fontname="helv", fontsize=9)

        # 注釈テキスト書き換え
        text_instances = page.search_for("3,000 THB")
        for rect in text_instances:
            target_rect = fitz.Rect(rect.x0, rect.y0 - 1, rect.x0 + 350, rect.y1 + 1)
            page.add_redact_annot(target_rect, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((rect.x0, rect.y1 - 1), "100 USD a separate shipping fee will be charged.", fontname="helv", fontsize=8)

        text_instances_term = page.search_for("1 million THB")
        for rect in text_instances_term:
            page.add_redact_annot(rect, fill=(1, 1, 1))
            page.apply_redactions()
            page.insert_text((rect.x0, rect.y1 - 1), "30,000 USD.", fontname="helv", fontsize=8.5)

        # 2. 明細行数値の抽出とUSD換算
        text_page = page.get_text("words")
        rows = {}
        for w in text_page:
            y0, y1, text = w[1], w[3], w[4]
            if y0 < 100 or y0 > 800: continue
            
            found_y = None
            for r_y in rows.keys():
                if abs(r_y - y0) < 4:
                    found_y = r_y
                    break
            if found_y is None:
                found_y = y0
                rows[found_y] = []
            rows[found_y].append(w)

        for y_key in sorted(rows.keys()):
            row_words = sorted(rows[y_key], key=lambda x: x[0])
            line_str = " ".join([w[4] for w in row_words]).upper()

            num_words = [w for w in row_words if re.match(r'^[\d,.-]+\.\d{2}$', w[4])]

            is_total_row = "TOTAL" in line_str and not any(x in line_str for x in ["SUB", "GRAND", "SPECIAL", "VAT"])
            is_discount_row = "SPECIAL DISCOUNT" in line_str
            is_sub_total_row = "SUB TOTAL" in line_str
            is_grand_total_row = "GRAND TOTAL" in line_str

            if num_words and not any([is_total_row, is_discount_row, is_sub_total_row, is_grand_total_row]):
                if len(num_words) >= 4:
                    u1_val, u3_val = 0, 0
                    for idx, w in enumerate(num_words):
                        rect = fitz.Rect(w[0] + 0.5, w[1] - 0.5, w[2] - 0.5, w[3] + 0.5)
                        val_thb = Decimal(w[4].replace(',', ''))
                        val_usd = int((val_thb / rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                        
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                        page.apply_redactions()

                        draw_x = w[2] - 1
                        if idx == 1:
                            u1_val = val_usd
                            total_u1_acc += val_usd
                            x_walls["u1_amt"] = draw_x
                        elif idx == 3:
                            u3_val = val_usd
                            total_u3_acc += val_usd
                            x_walls["u3_amt"] = draw_x
                        elif idx == 4:
                            x_walls["total"] = draw_x
                            val_usd = u1_val + u3_val

                        text_usd = f"{val_usd:,}"
                        tw = fitz.get_text_length(text_usd, fontname="helv", fontsize=9)
                        page.insert_text((draw_x - tw, w[3] - 1), text_usd, fontname="helv", fontsize=9)

            elif any([is_total_row, is_discount_row, is_sub_total_row, is_grand_total_row]):
                if num_words:
                    for w in num_words:
                        rect = fitz.Rect(w[0] + 0.5, w[1] - 0.5, w[2] - 0.5, w[3] + 0.5)
                        page.add_redact_annot(rect, fill=(1, 1, 1))
                        page.apply_redactions()

                    if is_total_row:
                        if len(num_words) >= 3 and x_walls["u1_amt"] > 0 and x_walls["u3_amt"] > 0:
                            t1_usd = f"{total_u1_acc:,}"
                            tw1 = fitz.get_text_length(t1_usd, fontname="helv", fontsize=9)
                            page.insert_text((x_walls["u1_amt"] - tw1, num_words[0][3] - 1), t1_usd, fontname="helv", fontsize=9)
                            
                            t3_usd = f"{total_u3_acc:,}"
                            tw3 = fitz.get_text_length(t3_usd, fontname="helv", fontsize=9)
                            page.insert_text((x_walls["u3_amt"] - tw3, num_words[1][3] - 1), t3_usd, fontname="helv", fontsize=9)

                        target_val = (total_u1_acc + total_u3_acc) - accumulated_discount_usd

                    elif is_discount_row:
                        disc_val = int((Decimal(num_words[-1][4].replace(',', '')) / rate).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
                        accumulated_discount_usd += abs(disc_val)
                        target_val = disc_val

                    elif is_sub_total_row or is_grand_total_row:
                        target_val = (total_u1_acc + total_u3_acc) - accumulated_discount_usd

                    if x_walls["total"] > 0:
                        text_usd = f"{target_val:,}"
                        tw = fitz.get_text_length(text_usd, fontname="helv", fontsize=9)
                        page.insert_text((x_walls["total"] - tw, num_words[-1][3] - 1), text_usd, fontname="helv", fontsize=9)

    out_buffer = io.BytesIO()
    doc.save(out_buffer)
    doc.close()
    return out_buffer.getvalue()

# 変換実行ボタン
if st.button("USD変換を実行"):
    if not rate_input:
        st.error("為替レートを入力してください。")
    elif not uploaded_file:
        st.error("PDFファイルをアップロードしてください。")
    else:
        try:
            pdf_bytes = uploaded_file.read()
            result_pdf = process_pdf_bytes(pdf_bytes, rate_input)
            
            output_filename = f"USD_{uploaded_file.name}"
            st.success("変換が完了しました！")
            
            # ダウンロードボタンを表示
            st.download_button(
                label="📥 変換後のPDFをダウンロード",
                data=result_pdf,
                file_name=output_filename,
                mime="application/pdf"
            )
        except Exception as e:
            st.error(f"エラーが発生しました: {e}")