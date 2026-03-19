import streamlit as st
import requests
import json
import re
import pandas as pd
import numpy as np
import yfinance as yf
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ==========================================
# 1. 页面配置与 CSS 样式
# ==========================================
st.set_page_config(page_title="Super Committee AI (双擎无缝版)", layout="wide", page_icon="🏦")

st.markdown("""
    <style>
    .expert-card {
        background-color: #ffffff;
        border: 1px solid #e0e0e0;
        border-radius: 10px;
        padding: 20px;
        margin-bottom: 20px;
        height: 550px; 
        overflow-y: auto;
        box-shadow: 0 4px 6px rgba(0,0,0,0.05);
    }
    .score-badge {
        font-size: 18px; font-weight: 900; color: #d32f2f;
        background-color: #ffebee; padding: 5px 15px;
        border-radius: 5px; display: inline-block; margin-top: 15px;
        border: 1px solid #ffcdd2;
    }
    .expert-title { color: #1565C0; margin-bottom: 10px; border-bottom: 2px solid #e3f2fd; padding-bottom: 5px; }
    .day1-title { color: #2e7d32; margin-bottom: 10px; border-bottom: 2px solid #c8e6c9; padding-bottom: 5px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 安全认证与云端配置读取 (Secrets)
# ==========================================
def check_password():
    """密码门禁系统，防止匿名消耗 API 额度"""
    if st.session_state.get("password_correct", False):
        return True

    st.title("🔒 访问受限")
    st.info("此应用已开启【EODHD + Yahoo 双擎数据架构】，请输入访问密码。")
    password_input = st.text_input("请输入访问密码", type="password")
    
    correct_password = st.secrets.get("APP_PASSWORD", "admin")

    if st.button("登录"):
        if password_input == correct_password:
            st.session_state["password_correct"] = True
            st.rerun()
        else:
            st.error("密码错误")
    return False

if not check_password():
    st.stop()

try:
    EODHD_API_KEY = st.secrets["EODHD_API_KEY"]
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
    DEEPSEEK_BASE_URL = st.secrets.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
except FileNotFoundError:
    st.error("❌ 未找到 secrets.toml 配置文件。本地运行请创建 `.streamlit/secrets.toml`。云端部署请在 Settings 中配置 Secrets。")
    st.stop()
except KeyError as e:
    st.error(f"❌ 配置文件中缺少必要 Key: {e}")
    st.stop()

# ==========================================
# 3. 智能搜索双擎 (EODHD -> Yahoo)
# ==========================================
def search_ticker(query):
    """【搜索双擎】优先 EODHD，失败则替补 Yahoo Finance API"""
    if not query: return []
    results =[]
    
    query_upper = query.upper().strip()
    if '.' in query_upper:
        results.append(f"{query_upper} | [精确输入直达/Direct Override]")
        
    base_query = query_upper.split('.')[0] if '.' in query_upper else query_upper
    
    # 1. 尝试 EODHD 主引擎搜索
    eodhd_has_data = False
    try:
        url = f"https://eodhd.com/api/search/{base_query}?api_token={EODHD_API_KEY}&limit=15&fmt=json"
        res = requests.get(url, timeout=5).json()
        if isinstance(res, list) and len(res) > 0:
            eodhd_has_data = True
            for item in res:
                code_str = f"{item.get('Code')}.{item.get('Exchange')} | {item.get('Name')} ({item.get('Type', 'Unknown')})"
                if code_str not in results: results.append(code_str)
    except: pass

    # 2. 如果 EODHD 没搜出结果 (除直达外)，启动 Yahoo 替补搜索
    if not eodhd_has_data or len(results) <= 1:
        try:
            y_url = f"https://query2.finance.yahoo.com/v1/finance/search?q={base_query}"
            # 雅虎接口必须加请求头，否则会报 403
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64)'} 
            y_res = requests.get(y_url, headers=headers, timeout=5).json()
            
            for item in y_res.get('quotes', [])[:15]:
                sym = item.get('symbol')
                name = item.get('shortname') or item.get('longname', '')
                q_type = item.get('quoteType', 'Unknown')
                if sym:
                    code_str = f"{sym} | {name} ({q_type}) [Yahoo]"
                    if code_str not in results: results.append(code_str)
        except Exception as e:
            pass

    return results

# ==========================================
# 4. 满血版核心数据双擎 (EODHD -> Yahoo)
# ==========================================

def fetch_from_eodhd(ticker_code):
    """主引擎：EODHD"""
    try:
        url_fund = f"https://eodhd.com/api/fundamentals/{ticker_code}?api_token={EODHD_API_KEY}&fmt=json"
        res_req = requests.get(url_fund, timeout=10)
        
        # 权限或不存在拦截
        if res_req.status_code != 200:
            return None, f"EODHD HTTP {res_req.status_code}"
            
        res = res_req.json()
        if not isinstance(res, dict) or ('message' in res and 'General' not in res):
            return None, "EODHD 数据结构异常或拦截"

        g = res.get('General') or {}
        h = res.get('Highlights') or {}
        v = res.get('Valuation') or {}
        t = res.get('Technicals') or {}
        s = res.get('SharesStats') or {}
        
        asset_type = g.get('Type', 'Common Stock')
        
        # 附加技术指标
        latest_rsi, latest_macd = "N/A", "N/A"
        if asset_type in ['Common Stock', 'ETF']:
            try:
                rsi_data = requests.get(f"https://eodhd.com/api/technical/{ticker_code}?function=rsi&period=14&api_token={EODHD_API_KEY}&fmt=json", timeout=3).json()
                if isinstance(rsi_data, list) and rsi_data: latest_rsi = round(rsi_data[-1].get('rsi', 0), 2)
                macd_data = requests.get(f"https://eodhd.com/api/technical/{ticker_code}?function=macd&api_token={EODHD_API_KEY}&fmt=json", timeout=3).json()
                if isinstance(macd_data, list) and macd_data: 
                    latest_macd = {"MACD_Value": round(macd_data[-1].get('macd', 0), 4), "Signal_Line": round(macd_data[-1].get('signal', 0), 4)}
            except: pass

        packet = {
            "Meta": {
                "Asset_Type": asset_type,
                "Currency": g.get('CurrencyCode', 'USD'),
                "Sector": g.get('Sector'), 
                "Industry": g.get('Industry'),
                "Description": str(g.get('Description', ''))[:400]
            },
            "Valuation_&_Profitability": {
                "PE": v.get('TrailingPE'), "Forward_PE": v.get('ForwardPE'),
                "PB": v.get('PriceBookMRQ'), "EV_EBITDA": v.get('EnterpriseValueEbitda'),
                "ROE": h.get('ReturnOnEquityTTM'), "Operating_Margin": h.get('OperatingMarginTTM'),
                "Rule_of_40_Score": round(((h.get('RevenueGrowthYoY') or 0) + (h.get('OperatingMarginTTM') or 0)) * 100, 2)
            },
            "Technicals_&_Risk": {
                "Beta": t.get('Beta'), "50_Day_MA": t.get('50DayMA'), "200_Day_MA": t.get('200DayMA'), 
                "52W_High": t.get('52WeekHigh'), "Short_Ratio": t.get('ShortRatio'),
                "RSI_14Day": latest_rsi, "MACD_Latest": latest_macd
            }
        }

        if asset_type in['ETF', 'Fund', 'Mutual Fund']:
            etf_data = res.get('ETF_Data') or {}
            top_10 = etf_data.get('Top_10_Holdings') or {}
            packet["ETF_Specifics"] = {
                "Expense_Ratio": etf_data.get('NetExpenseRatio'), "Yield": etf_data.get('Yield'),
                "Top_5_Holdings": {k: v.get('Assets_%') for k, v in list(top_10.items())[:5]} if top_10 else "N/A"
            }
        else:
            recent_earnings = dict(list(((res.get('Earnings') or {}).get('History') or {}).items())[:4])
            fwd_estimates = dict(list(((res.get('Earnings') or {}).get('Trend') or {}).items())[:2])
            recent_fcf = {k: v.get('freeCashFlow') for k, v in list(((res.get('Financials') or {}).get('Cash_Flow', {}).get('yearly', {})).items())[:3]}
            
            rev_5yr, rnd_5yr = {}, {}
            for k, val in list(((res.get('Financials') or {}).get('Income_Statement', {}).get('yearly', {})).items())[:5]: 
                rev_5yr[k] = val.get('totalRevenue')
                rnd_5yr[k] = val.get('researchDevelopment')

            packet["Stock_Specifics"] = {
                "Debt_To_Equity": h.get('NetDebtToEquity'), "Dividend_Yield": h.get('DividendYield'),
                "Institutional_Percent": s.get('InstitutionsPercent'), "Free_Cash_Flow_History_3Y": recent_fcf,             
                "Revenue_History_5Y": rev_5yr, "R&D_Expense_History_5Y": rnd_5yr,                   
                "Earnings_Beat_Miss_Last_4Q": recent_earnings, "Forward_Consensus_Estimates": fwd_estimates     
            }
        return packet, None
    except Exception as e:
        return None, str(e)


def fetch_from_yahoo(ticker_code):
    """替补引擎：Yahoo Finance (完全免费，抗击打)"""
    try:
        # 交易所后缀映射 (EODHD -> Yahoo)
        mapping = {".SH": ".SS", ".KO": ".KS", ".T": ".T"}
        yf_ticker = ticker_code
        for eod_ext, yf_ext in mapping.items():
            if yf_ticker.endswith(eod_ext): yf_ticker = yf_ticker.replace(eod_ext, yf_ext)
            
        stock = yf.Ticker(yf_ticker)
        info = stock.info
        if not info or 'symbol' not in info:
            return None, "雅虎财经找不到该代码信息"

        asset_type = info.get('quoteType', 'EQUITY') 
        
        # 提取 6 个月收盘价计算技术面
        hist = stock.history(period="6mo")
        rsi_val, macd_val = "N/A", "N/A"
        if not hist.empty and len(hist) > 30:
            close = hist['Close']
            delta = close.diff()
            gain = (delta.where(delta > 0, 0)).rolling(window=14).mean()
            loss = (-delta.where(delta < 0, 0)).rolling(window=14).mean()
            rs = gain / loss
            rsi_val = round(100 - (100 / (1 + rs)).iloc[-1], 2)
            
            exp1 = close.ewm(span=12, adjust=False).mean()
            exp2 = close.ewm(span=26, adjust=False).mean()
            macd_line = exp1 - exp2
            signal_line = macd_line.ewm(span=9, adjust=False).mean()
            macd_val = {"MACD_Value": round(macd_line.iloc[-1], 4), "Signal_Line": round(signal_line.iloc[-1], 4)}

        rev_growth = info.get('revenueGrowth', 0) or 0
        op_margin = info.get('operatingMargins', 0) or 0

        packet = {
            "Meta": {
                "Asset_Type": asset_type,
                "Currency": info.get('currency', 'USD'),
                "Name": info.get('shortName', yf_ticker),
                "Sector": info.get('sector', 'N/A'), "Industry": info.get('industry', 'N/A'),
                "Description": str(info.get('longBusinessSummary', ''))[:400]
            },
            "Valuation_&_Profitability": {
                "PE": info.get('trailingPE', 'N/A'), "Forward_PE": info.get('forwardPE', 'N/A'),
                "PB": info.get('priceToBook', 'N/A'), "EV_EBITDA": info.get('enterpriseToEbitda', 'N/A'),
                "ROE": info.get('returnOnEquity', 'N/A'), "Operating_Margin": op_margin,
                "Rule_of_40_Score": round((rev_growth + op_margin) * 100, 2)
            },
            "Technicals_&_Risk": {
                "Beta": info.get('beta', 'N/A'), "50_Day_MA": info.get('fiftyDayAverage', 'N/A'), 
                "200_Day_MA": info.get('twoHundredDayAverage', 'N/A'), "52W_High": info.get('fiftyTwoWeekHigh', 'N/A'),
                "Short_Ratio": info.get('shortRatio', 'N/A'), "RSI_14Day": rsi_val, "MACD_Latest": macd_val
            }
        }

        if asset_type in ['ETF', 'MUTUALFUND']:
            packet["ETF_Specifics"] = {"Yield": info.get('yield', 'N/A'), "Total_Assets": info.get('totalAssets', 'N/A')}
        else:
            # 简化提取近期财务历史
            try:
                cf = stock.cash_flow
                fcf_hist = {str(k)[:10]: v for k, v in cf.loc['Free Cash Flow'].head(3).items()} if 'Free Cash Flow' in cf.index else "N/A"
            except: fcf_hist = "N/A"
            try:
                inc = stock.income_stmt
                rev_hist = {str(k)[:10]: v for k, v in inc.loc['Total Revenue'].head(4).items()} if 'Total Revenue' in inc.index else "N/A"
            except: rev_hist = "N/A"

            packet["Stock_Specifics"] = {
                "Debt_To_Equity": info.get('debtToEquity', 'N/A'), "Dividend_Yield": info.get('dividendYield', 'N/A'),
                "Institutional_Percent": info.get('heldPercentInstitutions', 'N/A'), "Free_Cash_Flow_History_3Y": fcf_hist,             
                "Revenue_History_4Y": rev_hist                       
            }
        return packet, None
    except Exception as e:
        return None, str(e)


def fetch_comprehensive_data(ticker_code):
    """★ 终极数据调度器：EODHD为主，Yahoo为辅 ★"""
    
    # 尝试 1：商业引擎 EODHD
    packet, eod_err = fetch_from_eodhd(ticker_code)
    if packet:
        return packet, "EODHD 商业 API", None
        
    # 尝试 2：替补引擎 Yahoo
    print(f"⚠️ EODHD 抓取失败 ({eod_err})，自动无缝切换到 Yahoo Finance...")
    packet, yf_err = fetch_from_yahoo(ticker_code)
    if packet:
        return packet, "Yahoo Finance 全球 API (替补生效)", None
        
    # 全部坠毁
    return None, "N/A", f"双擎失效。EODHD报错: {eod_err} | Yahoo报错: {yf_err}"

# ==========================================
# 5. 全明星专家与 Day1 Prompt 库
# ==========================================
def get_expert_prompts():
    return {
        "投资大师组": {
            "Warren Buffett (巴菲特)": "你寻找护城河、高ROE(>15%)、自由现金流充裕且债务极低的公司。评估管理层诚信与资本利用率，必须有安全边际。",
            "Charlie Munger (芒格)": "运用反向思维(Inversion)。重点指出这笔投资可能失败的3个致命原因。寻找业务简单且具备定价权的公司。",
            "Cathie Wood (木头姐)": "关注颠覆性创新(AI/基因/新能源)与未来5年营收指数级增长潜力(TAM)。忽略短期PE估值。",
            "Michael Burry (大空头)": "深度逆向投资者。寻找财务裂缝、极端估值泡沫、或被忽视的周期性结构崩塌风险。寻找做空理由。",
            "Peter Lynch (彼得·林奇)": "寻找‘10倍股’。关注PEG(<1)、业务常识、机构持仓比(越低越好)以及资产负债表的强健程度。",
            "George Soros (索罗斯)": "运用反身性理论。分析当前市场共识偏见，寻找价格与基本面发生严重背离的暴涨或暴跌转折点。",
            "Ray Dalio (雷·达利欧)": "宏观原则与风险平价。分析该资产在当前利率/通胀周期中的表现，评估其Beta波动性对组合的破坏力。",
            "Bill Ackman (比尔·阿克曼)": "激进价值投资。寻找现金流强劲但管理层低效、可通过激进变革(回购、换CEO)释放巨大价值的标的。",
            "Phil Fisher (菲尔·费雪)": "成长股鼻祖。重点评估研发(R&D)支出的有效性、销售利润率提升空间及长期成长远见。",
            "Stanley Druckenmiller (德鲁肯米勒)": "宏观趋势交易。寻找具备‘不对称盈亏比’的标的。关注盈利动能的加速拐点，而非绝对估值。",
            "Mohnish Pabrai (帕布莱)": "Dhandho原则：‘正面我赢，反面我损失不多’。寻找极低下行风险的高确定性机会。",
            "Ben Graham (格雷厄姆)": "纯粹的量化价值分析。极度保守，严格看重市净率(PB)、低市盈率(PE)和净流动资产，必须有绝对安全边际。"
        },
        "投资专家组": {
            "Goldman Sachs (高盛高级分析师)": "You are a senior equity analyst at Goldman Sachs. Analyze: P/E compared to sector averages, Revenue growth trends over the last 5 years, Debt-to-equity health check, Dividend yield and payout sustainability score, Competitive moat rating (weak, moderate, strong), Bull case and bear case price targets for 12 months, Risk rating on a scale of 1-10 with clear reasoning, Entry price zones and stop-loss suggestions",
            "Morgan Stanley (大摩DCF估值VP)": "You are a VP-level investment banker at Morgan Stanley. Build a full DCF perspective: Revenue projection with growth assumptions, Operating margin estimates based on historical trends, Free cash flow calculations year by year (use provided FCF data), Weighted average cost of capital (WACC) estimate based on Beta, Clear verdict: undervalued, fairly valued, or overvalued, Key assumptions that could break the model",
            "Bridgewater (桥水资深风险官)": "You are a senior risk analyst at Bridgewater Associates. Evaluate: Sector concentration risk, Interest rate sensitivity for this position (analyze Beta and Debt), Recession stress test showing estimated drawdown, Liquidity risk rating, Tail risk scenarios with probability estimates, Hedging strategies to reduce top risks",
            "JPMorgan (小摩财报策略师)": "You are a senior equity research analyst at JPMorgan Chase. Deliver an earnings analysis: Last 4 quarters earnings vs estimates (beat or miss history), Key metrics Wall Street is watching (Forward Consensus), Segment-by-segment revenue breakdown trends, Options market implied move for earnings day, Bull case scenario and price impact estimate, Bear case scenario and downside risk estimate, My recommended play: buy, sell, or wait",
            "BlackRock (贝莱德多资产策略师)": "You are a senior portfolio strategist at BlackRock. Create: Exact asset allocation perspective, Core holdings vs satellite positions clearly labeled, Expected annual return range based on historical data, Expected maximum drawdown in a bad year, Rebalancing schedule and trigger rules",
            "Citadel (城堡高级量化交易员)": "You are a senior quantitative trader at Citadel. Analyze: Current trend direction, Key support and resistance levels based on 52W High/Low, Moving average analysis (50-day, 200-day) and crossover signals, RSI, MACD readings interpretation, Ideal entry price, stop-loss level, and profit target, Risk-to-reward ratio for the current setup, Confidence rating: strong buy, buy, neutral, sell, strong sell",
            "Harvard (哈佛捐赠基金策略师)": "You are the chief investment strategist for Harvard's endowment fund. Build a dividend perspective: Dividend yield and safety score (1-10 scale), Payout ratio analysis to flag any unsustainable dividends, Monthly income projection potential, DRIP reinvestment projection showing compounding, Ranked safety of this pick for long-term hold",
            "Bain (贝恩资深战略合伙人)": "You are a senior partner at Bain & Company. Provide a competitive landscape report: Top competitors in the sector, Revenue and profit margin comparison, Competitive moat analysis (brand, cost, network, switching), Management quality rating, Innovation pipeline and R&D spending (Use R&D Expense History), Biggest threats to the sector (regulation, disruption, macro), SWOT analysis",
            "Renaissance (文艺复兴量化研究员)": "You are a quantitative researcher at Renaissance Technologies. Identify hidden patterns: Insider buying and selling patterns (use Insider_Percent), Institutional ownership trend, Short interest analysis and squeeze potential (use Short_Ratio), Price behavior around earnings (pre-run, post-gap patterns), Statistical edge summary: what gives this stock a quantifiable advantage",
            "McKinsey (麦肯锡宏观合伙人)": "You are a senior partner at McKinsey's Global Institute. Analyze macro impacts: Current interest rate environment and its impact on this specific asset, Inflation trend analysis and whether it benefits or suffers, US dollar strength impact (domestic vs international), Global risk factors (geopolitics, trade wars, supply chains) affecting this company"
        }
    }

def get_day1_modules():
    return {
        "🧭 模块 1：核心基本面与催化剂 (Fundamentals & Catalyst)": "系统性分析其商业模式、营收质量（通过现金流印证）。判断近 6 个月内可能引发股价重估的催化剂是什么？",
        "🧮 模块 2：多维估值矩阵 (Valuation Matrix)": "综合评估其 EV/EBITDA 倍数、Rule of 40（营收增长+利润率）健康度、自由现金流。判断当前市场定价是计入了悲观预期还是透支了未来？",
        "⚖️ 模块 3：投资哲学交叉验证 (Cross-Philosophies)": "用三大哲学交叉审视：1.老虎基金视角（基本面做多/做空的逻辑是什么？） 2. 橡树资本视角（当前价格具备足够的安全边际吗？） 3. 德鲁肯米勒视角（宏观流动性与行业趋势是顺风还是逆风？）",
        "🚨 模块 4：事前检查与行动计划 (Pre-Check & Action Plan)": "【反偏见核心排雷】请执行‘事前检查’：假设该笔投资在 2 年后亏损了 50%，倒推最可能导致暴跌的 3 个致命原因。最后，给出极其明确的建仓策略（如观察哪些红旗指标，在什么支撑位建仓）。"
    }

# ==========================================
# 6. AI 评估引擎 (动态视角 + 4000 超长输出)
# ==========================================
def get_ai_response(name, role_desc, data, is_day1=False):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    
    asset_type = data.get('Meta', {}).get('Asset_Type', 'Unknown')
    currency = data.get('Meta', {}).get('Currency', 'USD')
    
    currency_prompt = f"【🚨极其重要】：该资产财务数据计价货币为 **{currency}**。请在分析绝对金额时，务必结合该国货币单位及当地宏观背景，避免产生估值幻觉。"
    
    try:
        if not is_day1:
            prompt = f"""
            你现在是 {name}。
            分析框架与任务：{role_desc}
            
            当前标的类型为 **{asset_type}**。{currency_prompt}
            1. 若是【EQUITY (股票)】，严格执行财务分析/DCF模型。
            2. 若是【ETF / Fund (基金)】，立即转换视角为“投资组合配置”！基于费率和整体表现分析。
            3. 若是【商品】，请忽略股权指标，纯粹从实际利率、避险及技术面评估。

            数据包（缺失请推理，绝不报错）：
            {json.dumps(data, ensure_ascii=False)}
            
            输出规则：
            1. 必须使用**中文**回答。利用具体数字支撑观点。排版使用 Markdown。
            2. 【绝对要求】在回答的最后另起一行，必须输出评分，格式精确包含“评分：X/10”。
            """
        else:
            prompt = f"""
            你正在执行顶级对冲基金的【Day1 Global 深度投研框架】模块：{name}。
            核心指令：{role_desc}
            
            当前标的类型为 **{asset_type}**。{currency_prompt}
            
            全量数据：
            {json.dumps(data, ensure_ascii=False)}
            
            要求：必须使用**中文**结构化排版。极度客观，用真实数字作论据。（不需要评分）
            """
            
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000, temperature=0.6, timeout=120 
        )
        content = resp.choices[0].message.content.strip()
        
        if not is_day1:
            score_match = re.search(r'评分[：:\s]*([0-9.]+)', content)
            if score_match:
                score = f"{score_match.group(1)}/10"
                main_text = re.sub(r'【?评分[：:\s]*([0-9.]+).*', '', content, flags=re.IGNORECASE|re.DOTALL).strip()
            else:
                score = "未评"
                main_text = content
            return name, main_text, score
        else:
            return name, content, None
            
    except Exception as e:
        return name, f"⚠️ 评估失败: {str(e)}", "Error"

# ==========================================
# 7. UI 主界面渲染
# ==========================================
st.title("🏆 Super Committee AI (双擎防弹版)")
st.caption("22 位投资大师与投研框架 · 完美支持日本/韩国/中国及欧美全市场")
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("🔍 搜索全球标的 (如: AAPL, 7203.T, 1605.T, 005930.KO, 600519.SS)", placeholder="支持美股、日韩股、A/港股及ETF")
    
selected_ticker = None
if search_query:
    search_results = search_ticker(search_query)
    if search_results:
        choice = st.selectbox("🎯 请确认目标标的：", search_results)
        selected_ticker = choice.split("|")[0].strip()
    else:
        st.warning("未找到匹配标的，请检查拼写。")

if st.button("🚀 启动全维度深度扫描") and selected_ticker:
    with st.spinner(f"正在智能调度数据引擎获取 {selected_ticker} 数据..."):
        rich_data, source_engine, error_log = fetch_comprehensive_data(selected_ticker)
    
    if rich_data:
        currency = rich_data.get('Meta', {}).get('Currency', 'USD')
        # ★ 向用户骄傲地展示您强大的高可用架构！
        st.success(f"✅ 数据提取成功！(数据源: **{source_engine}** | 计价货币: **{currency}**) 正在唤醒全网模型并发推理...")
        
        experts = get_expert_prompts()
        day1_modules = get_day1_modules()
        total_tasks = len(experts["投资大师组"]) + len(experts["投资专家组"]) + len(day1_modules)
        
        tab_m, tab_i, tab_day1 = st.tabs(["🌟 传奇大师意见 (12位)", "🏛️ 机构专家评估 (10位)", "🌐 深度投研 (4大模块)"])
        
        placeholders = {}
        with tab_m:
            cols_m = st.columns(2)
            for i, name in enumerate(experts["投资大师组"].keys()):
                with cols_m[i%2]:
                    placeholders[name] = {"ui": st.container(height=550, border=True), "is_day1": False}
                    placeholders[name]["ui"].info(f"⏳ {name} 正在深度审阅...")
        
        with tab_i:
            cols_i = st.columns(2)
            for i, name in enumerate(experts["投资专家组"].keys()):
                with cols_i[i%2]:
                    placeholders[name] = {"ui": st.container(height=550, border=True), "is_day1": False}
                    placeholders[name]["ui"].info(f"⏳ {name} 正在构建报告...")
                    
        with tab_day1:
            st.info("💡 **Day1 Global 投研框架** 专注于基本面重构、估值交叉验证与“事前检查”防雷。")
            for name in day1_modules.keys():
                placeholders[name] = {"ui": st.container(border=True), "is_day1": True}
                placeholders[name]["ui"].info(f"⏳ {name} 正在计算并生成...")

        completed = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures = []
            for name, desc in experts["投资大师组"].items():
                futures.append(executor.submit(get_ai_response, name, desc, rich_data, False))
            for name, desc in experts["投资专家组"].items():
                futures.append(executor.submit(get_ai_response, name, desc, rich_data, False))
            for name, desc in day1_modules.items():
                futures.append(executor.submit(get_ai_response, name, desc, rich_data, True))
            
            for future in as_completed(futures):
                name, text, score = future.result()
                completed += 1
                
                progress_bar.progress(completed / total_tasks)
                status_text.markdown(f"**⚡ 评估进行中: {completed}/{total_tasks} 个模块已完成...**")
                
                is_day1 = placeholders[name]["is_day1"]
                ui = placeholders[name]["ui"]
                
                with ui:
                    ui.empty()
                    if is_day1:
                        st.markdown(f"<h3 class='day1-title'>{name}</h3>", unsafe_allow_html=True)
                        st.markdown(text)
                    else:
                        st.markdown(f"<h3 class='expert-title'>👤 {name}</h3>", unsafe_allow_html=True)
                        st.markdown(text)
                        score_color = "#d32f2f" if score in ["未评", "Error"] else "#1565C0"
                        st.markdown(f"<hr style='margin: 10px 0;'><div class='score-badge' style='color:{score_color}'>🎯 评分：{score}</div>", unsafe_allow_html=True)
        
        status_text.success(f"✅ 全盘扫描完成！请在上方切换标签页查阅报告。")
        progress_bar.empty()
    else:
        st.error(f"❌ 数据抓取失败！\n\n**开发者诊断日志:**\n```\n{error_log}\n```\n请检查标的代码是否正确。")
