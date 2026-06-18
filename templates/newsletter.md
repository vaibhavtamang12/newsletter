# 📊 {{ newsletter_title }}
**Date:** {{ date }} | **Curated by:** AI & Market Analysts

---

{% if executive_summary %}
## Executive Summary

{{ executive_summary }}

{% endif %}
{% if key_themes %}
## Key Themes Today

{% for theme in key_themes %}- {{ theme }}
{% endfor %}
{% endif %}
## 📰 Top Stories

{% if articles %}
{% for article in articles %}
### {{ article.title }}
* **Source:** {{ article.source }}
* **Published:** {{ article.published }}
* **Link:** [Read Full Article]({{ article.link }})

{% if article.concise_summary %}**📝 Summary:** {{ article.concise_summary }}
{% endif %}
{% if article.why_it_matters %}**💡 Why it matters:** {{ article.why_it_matters }}
{% endif %}
{% if article.market_impact %}**📈 Market Impact:** {{ article.market_impact }}
{% endif %}

---
{% endfor %}
{% else %}
*No articles met quality thresholds today. Check back tomorrow.*

---
{% endif %}
{% if global_markets %}
## 🌐 Global Market Pulse

| Index | WTD | MTD | QTD | YTD |
|---|---|---|---|---|
{% for row in global_markets %}| {{ row.name }} | {{ row.wtd }} | {{ row.mtd }} | {{ row.qtd }} | {{ row.ytd }} |
{% endfor %}
{% if global_market_insights %}
{% for insight in global_market_insights %}- {{ insight }}
{% endfor %}
{% endif %}

---
{% endif %}
{% if style_factors %}
## 🇺🇸 US Style Factor Performance

| Factor | WTD | MTD | QTD | YTD |
|---|---|---|---|---|
{% for row in style_factors %}| {{ row.name }} | {{ row.wtd }} | {{ row.mtd }} | {{ row.qtd }} | {{ row.ytd }} |
{% endfor %}

---
{% endif %}
{% if sector_performance %}
## 📊 S&P 500 Sector Performance

| Sector | WTD | MTD | QTD | YTD |
|---|---|---|---|---|
{% for row in sector_performance %}| {{ row.name }} | {{ row.wtd }} | {{ row.mtd }} | {{ row.qtd }} | {{ row.ytd }} |
{% endfor %}

---
{% endif %}
**Disclaimer:** This material is provided for informational and educational purposes only and does not constitute investment, financial, legal, accounting, or tax advice. Investing involves risk, including possible loss of principal. Past performance is not indicative of future results. Readers should consult with their registered investment advisor before making any investment decisions.

*Generated on {{ generated_at_ist }} ({{ generated_at }})*
*Manage preferences: Unsubscribe | View Archive*
