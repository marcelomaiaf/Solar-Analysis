from __future__ import annotations

from pathlib import Path
from textwrap import wrap

from PIL import Image, ImageDraw, ImageFont
from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4, landscape
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image as RLImage,
    KeepTogether,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)
from reportlab.platypus.flowables import Flowable


ROOT = Path(__file__).resolve().parents[2]
OUT_DIR = ROOT / "output" / "pdf"
ASSET_DIR = OUT_DIR / "assets"
PDF_PATH = OUT_DIR / "relatorio-dag-weg-analysis-apolo.pdf"
AIRFLOW_SCREENSHOT = ASSET_DIR / "airflow-weg-analysis-graph-view.png"
AIRFLOW_CROP = ASSET_DIR / "airflow-weg-analysis-graph-crop.png"
DATA_FLOW_IMG = ASSET_DIR / "solar-analysis-data-flow.png"


PALETTE = {
    "navy": colors.HexColor("#172A3A"),
    "blue": colors.HexColor("#2563EB"),
    "teal": colors.HexColor("#0F766E"),
    "green": colors.HexColor("#15803D"),
    "amber": colors.HexColor("#B45309"),
    "red": colors.HexColor("#B91C1C"),
    "muted": colors.HexColor("#64748B"),
    "line": colors.HexColor("#CBD5E1"),
    "soft_blue": colors.HexColor("#EFF6FF"),
    "soft_teal": colors.HexColor("#ECFDF5"),
    "soft_amber": colors.HexColor("#FFF7ED"),
    "soft_gray": colors.HexColor("#F8FAFC"),
}


def register_fonts() -> tuple[str, str]:
    regular = Path("C:/Windows/Fonts/arial.ttf")
    bold = Path("C:/Windows/Fonts/arialbd.ttf")
    if regular.exists() and bold.exists():
        pdfmetrics.registerFont(TTFont("ReportArial", str(regular)))
        pdfmetrics.registerFont(TTFont("ReportArialBold", str(bold)))
        return "ReportArial", "ReportArialBold"
    return "Helvetica", "Helvetica-Bold"


FONT, FONT_BOLD = register_fonts()


class HeaderBar(Flowable):
    def __init__(self, title: str, subtitle: str):
        super().__init__()
        self.title = title
        self.subtitle = subtitle
        self.width = 0
        self.height = 2.25 * cm

    def draw(self) -> None:
        c = self.canv
        w = self._doctemplate.pagesize[0] - 2 * self._doctemplate.leftMargin
        c.setFillColor(PALETTE["navy"])
        c.roundRect(0, 0, w, self.height, 10, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, 18)
        c.drawString(0.55 * cm, 1.35 * cm, self.title)
        c.setFont(FONT, 8.8)
        c.setFillColor(colors.HexColor("#DCEAFE"))
        c.drawString(0.55 * cm, 0.68 * cm, self.subtitle)
        c.setFillColor(colors.HexColor("#22C55E"))
        c.roundRect(w - 5.05 * cm, 0.64 * cm, 4.45 * cm, 0.75 * cm, 7, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, 8.2)
        c.drawCentredString(w - 2.82 * cm, 0.88 * cm, "Monitoramento ativo diário")


class SectionLabel(Flowable):
    def __init__(self, label: str, color=PALETTE["blue"]):
        super().__init__()
        self.label = label
        self.color = color
        self.width = 0
        self.height = 0.55 * cm

    def draw(self) -> None:
        c = self.canv
        c.setFillColor(self.color)
        c.roundRect(0, 0.07 * cm, 3.6 * cm, 0.42 * cm, 5, fill=1, stroke=0)
        c.setFillColor(colors.white)
        c.setFont(FONT_BOLD, 7.5)
        c.drawCentredString(1.8 * cm, 0.19 * cm, self.label.upper())


class PipelineFlow(Flowable):
    def __init__(self):
        super().__init__()
        self.width = 25 * cm
        self.height = 4.45 * cm

    def draw(self) -> None:
        c = self.canv
        stages = [
            ("Plantas", "Postgres, ACL e metadados", PALETTE["soft_blue"], PALETTE["blue"]),
            ("Credenciais", "descriptografia segura", PALETTE["soft_teal"], PALETTE["teal"]),
            ("Clima + telemetria", "irradiância e geração real", PALETTE["soft_gray"], PALETTE["navy"]),
            ("Esperado vs real", "desvio, kWh e R$", PALETTE["soft_amber"], PALETTE["amber"]),
            ("Diagnóstico LLM", "causa provável e ação", PALETTE["soft_blue"], PALETTE["blue"]),
            ("Email condicional", "somente se houver desvio", PALETTE["soft_teal"], PALETTE["green"]),
        ]
        box_w = 3.65 * cm
        gap = 0.42 * cm
        y = 1.2 * cm
        for idx, (title, sub, fill, stroke) in enumerate(stages):
            x = idx * (box_w + gap)
            c.setStrokeColor(stroke)
            c.setFillColor(fill)
            c.roundRect(x, y, box_w, 1.35 * cm, 8, fill=1, stroke=1)
            c.setFillColor(PALETTE["navy"])
            c.setFont(FONT_BOLD, 8.8)
            c.drawCentredString(x + box_w / 2, y + 0.82 * cm, title)
            c.setFillColor(PALETTE["muted"])
            c.setFont(FONT, 6.8)
            c.drawCentredString(x + box_w / 2, y + 0.42 * cm, sub)
            if idx < len(stages) - 1:
                arrow_x = x + box_w + 0.06 * cm
                c.setStrokeColor(PALETTE["muted"])
                c.setLineWidth(1.2)
                c.line(arrow_x, y + 0.68 * cm, arrow_x + gap - 0.12 * cm, y + 0.68 * cm)
                c.setFillColor(PALETTE["muted"])
                p = c.beginPath()
                p.moveTo(arrow_x + gap - 0.12 * cm, y + 0.68 * cm)
                p.lineTo(arrow_x + gap - 0.28 * cm, y + 0.78 * cm)
                p.lineTo(arrow_x + gap - 0.28 * cm, y + 0.58 * cm)
                p.close()
                c.drawPath(p, fill=1, stroke=0)
        c.setFillColor(PALETTE["muted"])
        c.setFont(FONT, 7.2)
        c.drawString(
            0,
            0.36 * cm,
            "A mesma rotina pode atender qualquer usina solar cadastrada, desde que existam metadados técnicos e conector de telemetria.",
        )


class GitHubLink(Flowable):
    def __init__(self, url: str):
        super().__init__()
        self.url = url
        self.width = 13 * cm
        self.height = 0.75 * cm

    def draw(self) -> None:
        c = self.canv
        icon_x = 0.05 * cm
        icon_y = 0.08 * cm
        icon_size = 0.55 * cm
        cx = icon_x + icon_size / 2
        cy = icon_y + icon_size / 2

        c.setFillColor(colors.HexColor("#111827"))
        c.circle(cx, cy, icon_size / 2, fill=1, stroke=0)
        c.setStrokeColor(colors.white)
        c.setFillColor(colors.white)
        c.setLineWidth(1.2)
        # Small branch mark inside the circle: compact GitHub-style repository symbol.
        c.circle(cx - 0.11 * cm, cy + 0.09 * cm, 0.045 * cm, fill=1, stroke=0)
        c.circle(cx + 0.11 * cm, cy + 0.09 * cm, 0.045 * cm, fill=1, stroke=0)
        c.circle(cx, cy - 0.11 * cm, 0.045 * cm, fill=1, stroke=0)
        c.line(cx - 0.07 * cm, cy + 0.06 * cm, cx - 0.01 * cm, cy - 0.07 * cm)
        c.line(cx + 0.07 * cm, cy + 0.06 * cm, cx + 0.01 * cm, cy - 0.07 * cm)

        link_x = icon_x + icon_size + 0.25 * cm
        link_y = 0.22 * cm
        c.setFont(FONT_BOLD, 9)
        c.setFillColor(PALETTE["blue"])
        c.drawString(link_x, link_y, self.url)
        text_w = c.stringWidth(self.url, FONT_BOLD, 9)
        c.setStrokeColor(PALETTE["blue"])
        c.setLineWidth(0.7)
        c.line(link_x, link_y - 0.03 * cm, link_x + text_w, link_y - 0.03 * cm)
        c.linkURL(self.url, (link_x, link_y - 0.05 * cm, link_x + text_w, link_y + 0.28 * cm), relative=1)


def crop_airflow_graph() -> None:
    img = Image.open(AIRFLOW_SCREENSHOT).convert("RGB")
    # Crop the Airflow graph canvas and remove the task detail/log panel.
    crop = img.crop((70, 58, 1135, 990))
    crop.save(AIRFLOW_CROP)


def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    path = "C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"
    return ImageFont.truetype(path, size)


def rounded_box(draw: ImageDraw.ImageDraw, xy, fill, outline, radius=16, width=3):
    draw.rounded_rectangle(xy, radius=radius, fill=fill, outline=outline, width=width)


def arrow(draw: ImageDraw.ImageDraw, start, end, fill="#64748B", width=4):
    draw.line([start, end], fill=fill, width=width)
    ex, ey = end
    sx, sy = start
    if ex > sx:
        pts = [(ex, ey), (ex - 16, ey - 9), (ex - 16, ey + 9)]
    elif ex < sx:
        pts = [(ex, ey), (ex + 16, ey - 9), (ex + 16, ey + 9)]
    elif ey > sy:
        pts = [(ex, ey), (ex - 9, ey - 16), (ex + 9, ey - 16)]
    else:
        pts = [(ex, ey), (ex - 9, ey + 16), (ex + 9, ey + 16)]
    draw.polygon(pts, fill=fill)


def draw_wrapped(draw, xy, text, max_chars, font_obj, fill="#172A3A", line_gap=4):
    x, y = xy
    for line in wrap(text, max_chars):
        draw.text((x, y), line, font=font_obj, fill=fill)
        y += font_obj.size + line_gap
    return y


def make_data_flow_diagram() -> None:
    img = Image.new("RGB", (1450, 860), "#FFFFFF")
    d = ImageDraw.Draw(img)
    title_f = font(38, True)
    body_f = font(23)
    small_f = font(19)
    d.text((45, 32), "Fluxo de dados da Solar-Analysis", font=title_f, fill="#172A3A")
    d.text(
        (45, 82),
        "Da base da Apolo até o diagnóstico e comunicação condicional ao dono da usina",
        font=body_f,
        fill="#64748B",
    )

    boxes = {
        "db": (55, 170, 330, 310, "#EFF6FF", "#2563EB", "Postgres Apolo", "plantas, grants, vendor links, coordenadas e parâmetros técnicos"),
        "creds": (430, 170, 705, 310, "#ECFDF5", "#0F766E", "Credenciais", "descriptografia para acessar o conector/API da planta"),
        "vendor": (805, 170, 1080, 310, "#F8FAFC", "#172A3A", "API solar", "telemetria real: potência e leituras do inversor/datalogger"),
        "weather": (430, 430, 705, 570, "#FFF7ED", "#B45309", "Clima", "irradiância, temperatura, vento, nuvens e chuva por coordenada"),
        "expected": (805, 430, 1080, 570, "#EFF6FF", "#2563EB", "Geração esperada", "modelo físico com área, eficiência, orientação e perdas"),
        "analysis": (1165, 300, 1410, 470, "#ECFDF5", "#15803D", "Análise ativa", "compara real vs esperado, estima perda, diagnostica causa e recomenda ação"),
        "email": (1165, 585, 1410, 730, "#FEF2F2", "#B91C1C", "Email ao dono", "enviado apenas quando a geração fica fora do intervalo esperado"),
    }
    for key, (x1, y1, x2, y2, fill, outline, title, desc) in boxes.items():
        rounded_box(d, (x1, y1, x2, y2), fill, outline)
        d.text((x1 + 22, y1 + 20), title, font=font(25, True), fill="#172A3A")
        draw_wrapped(d, (x1 + 22, y1 + 60), desc, 28, small_f, fill="#475569")

    arrow(d, (330, 240), (430, 240))
    arrow(d, (705, 240), (805, 240))
    arrow(d, (330, 278), (430, 500))
    arrow(d, (705, 500), (805, 500))
    arrow(d, (1080, 240), (1165, 360))
    arrow(d, (1080, 500), (1165, 390))
    arrow(d, (1288, 470), (1288, 585), fill="#B91C1C")
    d.text((60, 780), "Princípio de produto: a rotina deixa o usuário menos dependente de abrir portais técnicos e transforma dados diários em ação.", font=body_f, fill="#172A3A")
    img.save(DATA_FLOW_IMG)


def styles():
    base = getSampleStyleSheet()
    return {
        "title": ParagraphStyle(
            "ReportTitle",
            parent=base["Title"],
            fontName=FONT_BOLD,
            fontSize=16,
            leading=20,
            textColor=PALETTE["navy"],
            spaceAfter=8,
        ),
        "h2": ParagraphStyle(
            "H2",
            parent=base["Heading2"],
            fontName=FONT_BOLD,
            fontSize=12.5,
            leading=15,
            textColor=PALETTE["navy"],
            spaceBefore=6,
            spaceAfter=5,
        ),
        "h3": ParagraphStyle(
            "H3",
            parent=base["Heading3"],
            fontName=FONT_BOLD,
            fontSize=10.2,
            leading=12.2,
            textColor=PALETTE["navy"],
            spaceBefore=6,
            spaceAfter=3,
        ),
        "body": ParagraphStyle(
            "Body",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=8.8,
            leading=11.4,
            textColor=colors.HexColor("#263442"),
            alignment=TA_LEFT,
            spaceAfter=5,
        ),
        "small": ParagraphStyle(
            "Small",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=7.4,
            leading=9.2,
            textColor=PALETTE["muted"],
            spaceAfter=4,
        ),
        "caption": ParagraphStyle(
            "Caption",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=7,
            leading=8.5,
            textColor=PALETTE["muted"],
            alignment=TA_CENTER,
        ),
        "center": ParagraphStyle(
            "Center",
            parent=base["BodyText"],
            fontName=FONT,
            fontSize=8.4,
            leading=10.5,
            alignment=TA_CENTER,
            textColor=colors.HexColor("#263442"),
        ),
    }


def paragraph(text: str, style):
    return Paragraph(text.replace("\n", "<br/>"), style)


def build_pdf() -> None:
    crop_airflow_graph()
    make_data_flow_diagram()
    st = styles()
    doc = SimpleDocTemplate(
        str(PDF_PATH),
        pagesize=landscape(A4),
        leftMargin=1.0 * cm,
        rightMargin=1.0 * cm,
        topMargin=0.8 * cm,
        bottomMargin=0.8 * cm,
        title="Solar-Analysis - Apolo Ecotech",
        author="Codex",
    )

    story = []
    story.append(HeaderBar("Solar-Analysis", "Relatório executivo e técnico da rotina diária de análise de usinas solares"))
    story.append(Spacer(1, 0.25 * cm))
    story.append(SectionLabel("1. Negócio", PALETTE["teal"]))
    story.append(paragraph("O que foi desenvolvido?", st["h3"]))
    story.append(paragraph("A Solar-Analysis estrutura uma rotina diária para percorrer as usinas cadastradas na Apolo, coletar dados operacionais, estimar a geração esperada e comparar o resultado com a geração real. Embora a DAG seja chamada <b>WEG-Analysis</b>, a proposta de negócio é generalizável: qualquer usina solar cadastrada pode entrar no mesmo ciclo quando possui metadados técnicos, localização, credenciais e conector de telemetria.", st["body"]))
    story.append(paragraph("O desenvolvimento organiza dados de cadastro, credenciais, clima e telemetria em uma sequência única de análise. Essa base permite transformar leituras técnicas de inversores e condições ambientais em um diagnóstico de performance diário, comparável entre plantas e compreensível para usuários não técnicos.", st["body"]))
    story.append(paragraph("Contribuição para a Apolo Ecotech", st["h3"]))
    story.append(paragraph("A feature transforma a plataforma de um painel consultivo para uma camada de monitoramento ativo. Em vez de o usuário precisar abrir vários portais, interpretar métricas técnicas e perceber perdas tardiamente, a Apolo passa a acompanhar diariamente a performance e a entregar uma leitura objetiva sobre geração, desvio, perda estimada e causa provável.", st["body"]))
    story.append(paragraph("Para o dono da usina, isso aumenta previsibilidade sobre retorno financeiro e reduz a dependência de análise manual. Para integradores, cria uma rotina escalável de priorização: as plantas com desvio relevante ganham atenção primeiro, com contexto técnico suficiente para orientar suporte, manutenção e comunicação com o cliente.", st["body"]))
    story.append(paragraph("Fluxo ponta a ponta da rotina", st["h3"]))
    story.append(PipelineFlow())
    story.append(paragraph("O fluxo ponta a ponta segue a lógica: get_plant_data -> get_credentials -> get_telemetry -> get_weather -> get_expected_generation -> compare_generation_interval -> estimate_kwh_financial_loss -> llm_diagnosis -> send_conditional_email. O email é condicionado ao desvio: se a usina estiver dentro do intervalo esperado, a rotina registra a análise sem acionar o dono da usina; se estiver fora, o relatório leva contexto, impacto e recomendação.", st["body"]))
    story.append(paragraph("O relatório diário de performance aparece no planejamento do Módulo 2 como uma evolução aplicada do Apolo. Ele conecta produto, arquitetura, dados e inteligência artificial em uma entrega concreta para validar se o usuário entende melhor a performance da usina com uma leitura diária simplificada.", st["body"]))

    story.append(PageBreak())
    story.append(Spacer(1, 0.2 * cm))
    story.append(SectionLabel("2. Funcionamento", PALETTE["blue"]))
    story.append(paragraph("O print abaixo mostra o grafo executado no Airflow. À esquerda estão as tasks da DAG; à direita aparece o detalhe da execução da task selecionada. A DAG parte da base da Apolo, abre dois ramos principais - telemetria real e clima/geração esperada - e prepara os dados para a comparação operacional.", st["body"]))
    img1 = RLImage(str(AIRFLOW_CROP), width=11.9 * cm, height=10.4 * cm)
    img2 = RLImage(str(DATA_FLOW_IMG), width=11.9 * cm, height=7.05 * cm)
    visual_table = Table(
        [
            [img1, img2],
            [
                paragraph("Print do grafo da DAG no Airflow, com as tasks get_plant_data, get_credentials, get_telemetry, get_weather e get_expected_generation.", st["caption"]),
                paragraph("Diagrama do fluxo de dados: base Apolo, credenciais, API solar, clima, estimativa, análise ativa e email condicional.", st["caption"]),
            ],
        ],
        colWidths=[12.25 * cm, 12.25 * cm],
        rowHeights=[10.55 * cm, 0.85 * cm],
    )
    visual_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BOX", (0, 0), (-1, 0), 0.5, PALETTE["line"]),
        ("BACKGROUND", (0, 0), (-1, 0), colors.white),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ]))
    story.append(visual_table)

    story.append(PageBreak())
    story.append(Spacer(1, 0.15 * cm))
    story.append(SectionLabel("3. Tasks e conclusão", PALETTE["green"]))
    tasks = [
        ("get_plant_data", "Busca no Postgres todas as usinas e os dados necessários para análise: nome, timezone, área dos módulos, eficiência, localização, latitude, longitude, orientação, vínculo com fornecedor e credenciais criptografadas."),
        ("get_credentials", "Descriptografa as credenciais obtidas do banco, permitindo que a rotina consulte a API solar correta sem expor segredos no fluxo de relatório."),
        ("get_telemetry", "Consulta a telemetria real da usina na API do painel solar, especialmente a série de potência ao longo do dia, que depois sustenta o cálculo de geração realizada."),
        ("get_weather", "Coleta dados climáticos e de irradiância por coordenada e data, incluindo radiação solar, temperatura, vento, nuvens e chuva."),
        ("get_expected_generation", "Transforma clima e parâmetros técnicos da planta em uma estimativa de geração esperada usando modelo físico baseado em irradiância, área, eficiência, inclinação, azimute, perdas e temperatura."),
        ("compare_generation_interval", "Compara a geração real com a esperada e classifica se a usina está dentro do intervalo aceitável de desempenho."),
        ("estimate_kwh_financial_loss", "Quando há desvio relevante, estima a perda energética em kWh e a perda financeira associada."),
        ("llm_diagnosis", "Usa dados climáticos, telemetria, informações do inversor, alarmes, contexto da internet e histórico para explicar a causa mais provável e sugerir uma ação objetiva."),
        ("send_conditional_email", "Envia o relatório ao dono da usina somente quando a geração fica fora do intervalo esperado, evitando notificações sem necessidade."),
    ]
    task_rows = [[paragraph("<b>Task/etapa</b>", st["small"]), paragraph("<b>Responsabilidade no fluxo</b>", st["small"])]]
    for name, desc in tasks:
        task_rows.append([paragraph(f"<b>{name}</b>", st["small"]), paragraph(desc, st["small"])])
    task_table = Table(task_rows, colWidths=[5.0 * cm, 19.5 * cm])
    task_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), PALETTE["soft_blue"]),
        ("TEXTCOLOR", (0, 0), (-1, 0), PALETTE["navy"]),
        ("GRID", (0, 0), (-1, -1), 0.35, PALETTE["line"]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("LEFTPADDING", (0, 0), (-1, -1), 5),
        ("RIGHTPADDING", (0, 0), (-1, -1), 5),
        ("TOPPADDING", (0, 0), (-1, -1), 3.5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3.5),
    ]))
    story.append(task_table)
    story.append(Spacer(1, 0.25 * cm))
    story.append(paragraph("Conclusão", st["h3"]))
    conclusion = (
        "Na solução Apolo, essa feature entra como uma rotina operacional que roda todos os dias e muda a experiência do usuário: "
        "o dono da usina passa a receber uma explicação quando existe problema, enquanto o integrador ganha uma fila de atenção mais priorizada e com menos análise manual. "
        "O benefício central é monitoramento muito mais ativo: a plataforma deixa de apenas exibir dados e passa a identificar desvios, estimar impacto, apontar causa provável e orientar ação antes que a perda vire uma surpresa financeira."
    )
    story.append(paragraph(conclusion, st["body"]))
    story.append(Spacer(1, 0.2 * cm))
    story.append(GitHubLink("https://github.com/marcelomaiaf/Solar-Analysis"))

    def footer(canvas, doc):
        canvas.saveState()
        canvas.setFont(FONT, 7)
        canvas.setFillColor(PALETTE["muted"])
        canvas.drawString(doc.leftMargin, 0.35 * cm, "Apolo Ecotech - Solar-Analysis")
        canvas.drawRightString(doc.pagesize[0] - doc.rightMargin, 0.35 * cm, f"Pagina {doc.page}")
        canvas.restoreState()

    doc.build(story, onFirstPage=footer, onLaterPages=footer)


if __name__ == "__main__":
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ASSET_DIR.mkdir(parents=True, exist_ok=True)
    build_pdf()
    print(PDF_PATH)
