from reportlab.lib.pagesizes import letter
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer
from reportlab.lib.styles import getSampleStyleSheet

doc = SimpleDocTemplate("/home/claude/fact-check-agent/test_assets/trap_document.pdf", pagesize=letter)
styles = getSampleStyleSheet()
story = []

title = Paragraph("NexaCloud Solutions — Investor Briefing 2026", styles['Title'])
story.append(title)
story.append(Spacer(1, 16))

paras = [
    "NexaCloud Solutions was founded in 2015 and has rapidly become a leader in "
    "enterprise cloud infrastructure.",

    "In our most recent funding round, we raised $50 million in Series B financing, "
    "led by a consortium of top-tier venture capital firms.",

    "Our platform currently processes over 2 trillion API requests per day, more than "
    "any other provider in the industry.",

    "As of 2026, the global cloud computing market is valued at approximately $50,000 "
    "billion, reflecting explosive growth across all sectors.",

    "NexaCloud's flagship product achieved 99.999% uptime in 2025, a benchmark unmatched "
    "by any competitor including AWS or Azure.",

    "The company's headquarters relocated to Austin, Texas in January 2021 following a "
    "wave of tech migration out of California.",

    "NexaCloud's annual recurring revenue (ARR) reached $1.2 billion in fiscal year 2025, "
    "representing 340% year-over-year growth.",

    "Our engineering team has grown to over 50,000 employees worldwide as of this year.",

    "We were named the world's first carbon-negative cloud provider in 2023, a title "
    "verified by an independent sustainability audit.",

    "NexaCloud's market capitalization currently exceeds $3 trillion, placing it among "
    "the top five most valuable companies on Earth.",
]

for p in paras:
    story.append(Paragraph(p, styles['Normal']))
    story.append(Spacer(1, 10))

doc.build(story)
print("PDF created.")
