import os
import argparse
import pdfplumber
import fitz  # PyMuPDF


def group_words_into_lines(words, line_tol=2.0):
    """
    Cluster words into lines using their 'top' coordinate.
    根据单词的 'top' 坐标将其聚类成行。
    """
    if not words:
        return []

    # Sort words by top coordinate, then by x coordinate
    # 按 top 坐标排序，然后按 x 坐标排序
    words = sorted(words, key=lambda w: (float(w.get("top", 0.0)), float(w.get("x0", 0.0))))

    lines = []
    current = []
    current_top = None

    for w in words:
        top = float(w.get("top", 0.0))
        if current_top is None:
            current_top = top
            current = [w]
            continue

        # If the vertical distance is within tolerance, consider it the same line
        # 如果垂直距离在容差范围内，则认为是同一行
        if abs(top - current_top) <= line_tol:
            current.append(w)
            # running average stabilizes grouping
            # 使用移动平均值稳定分组
            current_top = (current_top * (len(current) - 1) + top) / len(current)
        else:
            lines.append(current)
            current = [w]
            current_top = top

    if current:
        lines.append(current)

    return lines


def build_line_text(line_words, space_unit_pts=3.0, min_spaces=1):
    """
    Rebuild a line by inserting spaces based on x-gaps.
    Returns (text, x0, x1, top, font_size_est).
    
    通过基于 x 轴间隙插入空格来重建一行文本。
    返回 (文本, x0, x1, top, 估计字体大小)。
    """
    # Sort words in the line by x-coordinate
    # 按 x 坐标对行中的单词进行排序
    line_words = sorted(line_words, key=lambda w: float(w.get("x0", 0.0)))

    # representative font size: median of sizes if present, else bbox height
    # 代表性字体大小：如果有尺寸信息则取中位数，否则取边界框高度
    sizes = []
    for w in line_words:
        s = w.get("size", None)
        if s is not None:
            try:
                sizes.append(float(s))
            except Exception:
                pass

    if sizes:
        sizes_sorted = sorted(sizes)
        font_size = float(sizes_sorted[len(sizes_sorted) // 2])
    else:
        # fallback: median bbox height
        # 后备方案：边界框高度的中位数
        hs = []
        for w in line_words:
            top = float(w.get("top", 0.0))
            bottom = float(w.get("bottom", top + 10.0))
            hs.append(max(6.0, bottom - top))
        hs.sort()
        font_size = float(hs[len(hs) // 2]) if hs else 10.0

    # Median top coordinate for the line
    # 该行的 top 坐标中位数
    top_med = sorted([float(w.get("top", 0.0)) for w in line_words])[len(line_words) // 2]

    first_x0 = float(line_words[0].get("x0", 0.0))
    # Initialize last_x1 and prev_x1
    # 初始化 last_x1 和 prev_x1
    last_x1 = float(line_words[0].get("x1", line_words[0].get("x0", 0.0)))
    prev_x1 = float(line_words[0].get("x1", line_words[0].get("x0", 0.0)))

    parts = [line_words[0].get("text", "")]

    for w in line_words[1:]:
        text = w.get("text", "")
        x0 = float(w.get("x0", 0.0))
        x1 = float(w.get("x1", x0))

        # Calculate gap between previous word end and current word start
        # 计算前一个单词结束和当前单词开始之间的间隙
        gap = x0 - prev_x1

        if gap > 0:
            # Calculate number of spaces to insert
            # 计算要插入的空格数
            n_spaces = int(round(gap / max(0.5, space_unit_pts)))
            n_spaces = max(min_spaces, n_spaces)
            parts.append(" " * n_spaces)
        else:
            # slight negative gaps happen; keep minimal separation only when it looks like a break
            # 可能会出现轻微的负间隙；仅在看起来像断开时保持最小间隔
            parts.append(" " if gap > -space_unit_pts * 0.3 else "")

        parts.append(text)
        prev_x1 = max(prev_x1, x1)
        last_x1 = max(last_x1, x1)

    return "".join(parts), first_x0, last_x1, top_med, font_size


def extract_lines_with_positions(pdf_path, line_tol=2.0, space_unit_pts=3.0, min_spaces=1):
    """
    Returns list per page: [(line_text, x0, top, font_size), ...]
    Coordinates are in PDF points with origin at top-left (like pdfplumber/PyMuPDF).
    
    返回每页的列表：[(line_text, x0, top, font_size), ...]
    坐标采用 PDF 点数，原点位于左上角（类似于 pdfplumber/PyMuPDF）。
    """
    pages_lines = []

    with pdfplumber.open(pdf_path) as pdf:
        for page in pdf.pages:
            # Extract words with extra attributes
            # 提取带有额外属性的单词
            words = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=False,
                extra_attrs=["size", "fontname"]
            )

            # Group words into lines
            # 将单词分组成行
            lines = group_words_into_lines(words, line_tol=line_tol)

            out = []
            for lw in lines:
                line_text, x0, x1, top, font_size = build_line_text(
                    lw, space_unit_pts=space_unit_pts, min_spaces=min_spaces
                )
                if line_text.strip():
                    out.append((line_text, x0, top, font_size))
            pages_lines.append(out)

    return pages_lines


def make_side_by_side(input_pdf, output_pdf, line_tol=2.0, space_unit_pts=3.0, min_spaces=1):
    """
    Output pages are double-width:
      left: original page
      right: rebuilt text drawn at approx original coordinates (x offset by page width)
      
    输出页面宽度加倍：
      左侧：原始页面
      右侧：在近似原始坐标处绘制的重建文本（x 坐标偏移页面宽度）
    """
    src = fitz.open(input_pdf)
    out = fitz.open()

    # Extract text lines and positions
    # 提取文本行和位置
    lines_per_page = extract_lines_with_positions(
        input_pdf, line_tol=line_tol, space_unit_pts=space_unit_pts, min_spaces=min_spaces
    )

    for i, src_page in enumerate(src):
        rect = src_page.rect
        w, h = rect.width, rect.height

        # Create new page with double width
        # 创建双倍宽度的新页面
        new_page = out.new_page(width=2 * w, height=h)

        # Left: embed original page as a vector “form”
        # 左侧：将原始页面作为矢量“表单”嵌入
        new_page.show_pdf_page(fitz.Rect(0, 0, w, h), src, i)

        # Right: draw rebuilt text
        # 右侧：绘制重建的文本
        x_off = w
        page_lines = lines_per_page[i] if i < len(lines_per_page) else []

        for (txt, x0, top, font_size) in page_lines:
            # y: pdfplumber 'top' is top of bbox; nudge toward baseline
            # y: pdfplumber 的 'top' 是边界框的顶部；向基线微调
            y = float(top) + float(font_size) * 0.85

            new_page.insert_text(
                fitz.Point(x_off + float(x0), float(y)),
                txt,
                fontsize=float(font_size),
                fontname="helv",     # built-in Helvetica / 内置 Helvetica 字体
                color=(0, 0, 0),     # black / 黑色
                overlay=True
            )

    out.save(output_pdf)
    out.close()
    src.close()
    print(f"Wrote / 已写入: {output_pdf}")


def make_overlay_white(input_pdf, output_pdf, line_tol=2.0, space_unit_pts=3.0, min_spaces=1):
    """
    Output is the original PDF with extracted text overlaid in white.
    This often “reveals” text on top of black redaction bars without detecting them.
    
    输出为原始 PDF，提取的文本以白色覆盖在上方。
    这通常可以在不检测黑色涂黑条的情况下“显示”其上方的文本。
    """
    doc = fitz.open(input_pdf)

    lines_per_page = extract_lines_with_positions(
        input_pdf, line_tol=line_tol, space_unit_pts=space_unit_pts, min_spaces=min_spaces
    )

    for i, page in enumerate(doc):
        page_lines = lines_per_page[i] if i < len(lines_per_page) else []
        for (txt, x0, top, font_size) in page_lines:
            y = float(top) + float(font_size) * 0.85
            page.insert_text(
                fitz.Point(float(x0), float(y)),
                txt,
                fontsize=float(font_size),
                fontname="helv",
                color=(1, 1, 1),   # white / 白色
                overlay=True
            )

    doc.save(output_pdf)
    doc.close()
    print(f"Wrote / 已写入: {output_pdf}")


def main():
    ap = argparse.ArgumentParser(description="PDF Redaction Text Recovery Tool / PDF 涂黑文本恢复工具")
    ap.add_argument("input_pdf", help="Path to input PDF / 输入 PDF 文件路径")
    ap.add_argument("-o", "--output", default=None, help="Output PDF path / 输出 PDF 文件路径")
    ap.add_argument("--mode", choices=["side_by_side", "overlay_white"], default="side_by_side", 
                    help="Output mode: side_by_side or overlay_white / 输出模式：并排显示或白字覆盖")
    ap.add_argument("--line-tol", type=float, default=2.0, 
                    help="Line grouping tolerance (pts). Try 1.5–4.0 / 行分组容差 (pts)。尝试 1.5–4.0")
    ap.add_argument("--space-unit", type=float, default=3.0, 
                    help="Pts per inserted space (bigger => fewer spaces) / 每个插入空格的 pts (越大 => 空格越少)")
    ap.add_argument("--min-spaces", type=int, default=1, 
                    help="Minimum spaces between words when gap exists / 单词间存在间隙时的最小空格数")
    args = ap.parse_args()

    if not os.path.exists(args.input_pdf):
        raise FileNotFoundError(f"{args.input_pdf} not found / 未找到文件: {args.input_pdf}")

    if args.output is None:
        base, _ = os.path.splitext(args.input_pdf)
        suffix = "_side_by_side.pdf" if args.mode == "side_by_side" else "_overlay_white.pdf"
        args.output = base + suffix

    if args.mode == "side_by_side":
        make_side_by_side(
            args.input_pdf, args.output,
            line_tol=args.line_tol, space_unit_pts=args.space_unit, min_spaces=args.min_spaces
        )
    else:
        make_overlay_white(
            args.input_pdf, args.output,
            line_tol=args.line_tol, space_unit_pts=args.space_unit, min_spaces=args.min_spaces
        )


if __name__ == "__main__":
    main()
