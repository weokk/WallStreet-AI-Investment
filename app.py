import streamlit as st
import requests
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ==========================================
# 1. 页面配置与 CSS 样式
# ==========================================
st.set_page_config(page_title="Super Committee AI V1 (Max Data)", layout="wide", page_icon="🏦")

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
    st.info("此应用已开启满血版数据引擎，每次查询消耗量较大，请输入访问密码。")
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

# 读取 API Key (从 Streamlit Secrets 读取)
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
# 3. 满血版核心数据引擎 (含技术指标加拨)
# ==========================================
def search_ticker(query):
    """模糊搜索"""
    if not query or not EODHD_API_KEY: return[]
    url = f"https://eodhd.com/api/search/{query}?api_token={EODHD_API_KEY}&fmt=json"
    try:
        res = requests.get(url, timeout=5).json()
        return [f"{item['Code']}.{item['Exchange']} | {item['Name']} ({item['Type']})" for item in res[:8]]
    except: return[]

def fetch_comprehensive_data(ticker_code):
    """满血全量数据抓取：包含基础面、历史财报与深度技术指标"""
    try:
        # 1. 抓取全量基本面 (消耗 1 API 请求)
        url_fund = f"https://eodhd.com/api/fundamentals/{ticker_code}?api_token={EODHD_API_KEY}&fmt=json"
        res = requests.get(url_fund, timeout=15).json()
        
        g = res.get('General') or {}
        h = res.get('Highlights') or {}
        v = res.get('Valuation') or {}
        t = res.get('Technicals') or {}
        
        asset_type = g.get('Type', 'Common Stock')
        
        # 2. 【新增】额外调用技术指标 API (消耗 2 API 请求，喂给 Citadel)
        latest_rsi = "N/A"
        latest_macd = "N/A"
        if asset_type in['Common Stock', 'ETF']:
            try:
                # RSI 14日
                url_rsi = f"https://eodhd.com/api/technical/{ticker_code}?function=rsi&period=14&api_token={EODHD_API_KEY}&fmt=json"
                rsi_data = requests.get(url_rsi, timeout=5).json()
                if rsi_data and isinstance(rsi_data, list): latest_rsi = round(rsi_data[-1].get('rsi', 0), 2)
                
                # MACD
                url_macd = f"https://eodhd.com/api/technical/{ticker_code}?function=macd&api_token={EODHD_API_KEY}&fmt=json"
                macd_data = requests.get(url_macd, timeout=5).json()
                if macd_data and isinstance(macd_data, list): 
                    latest_macd = {
                        "MACD_Value": round(macd_data[-1].get('macd', 0), 4),
                        "Signal_Line": round(macd_data[-1].get('signal', 0), 4),
                        "Divergence": round(macd_data[-1].get('divergence', 0), 4)
                    }
            except Exception as tech_e:
                print(f"技术指标获取失败: {tech_e}")

        packet = {
            "Meta": {
                "Asset_Type": asset_type,
                "Sector": g.get('Sector'), 
                "Industry": g.get('Industry'),
                "Description": g.get('Description', '')[:400]
            },
            "Valuation_&_Profitability": {
                "PE": v.get('TrailingPE'), "Forward_PE": v.get('ForwardPE'),
                "ROE": h.get('ReturnOnEquityTTM'), "Operating_Margin": h.get('OperatingMarginTTM')
            },
            "Technicals_&_Risk": {
                "Beta": t.get('Beta'), "50_Day_MA": t.get('50DayMA'), 
                "200_Day_MA": t.get('200DayMA'), "52W_High": t.get('52WeekHigh'),
                "Short_Ratio": t.get('ShortRatio'),
                "RSI_14Day": latest_rsi,       # <--- Citadel 狂喜
                "MACD_Latest": latest_macd     # <--- Citadel 狂喜
            }
        }

        # ETF 专属数据
        if asset_type in['ETF', 'Fund', 'Mutual Fund']:
            etf_data = res.get('ETF_Data') or {}
            top_10 = etf_data.get('Top_10_Holdings') or {}
            packet["ETF_Specifics"] = {
                "Expense_Ratio": etf_data.get('NetExpenseRatio'),
                "Yield": etf_data.get('Yield'),
                "Top_5_Holdings": {k: v.get('Assets_%') for k, v in list(top_10.items())[:5]} if top_10 else "N/A",
                "Asset_Allocation": etf_data.get('Asset_Allocation')
            }
            
        # 股票专属深度数据
        elif asset_type == 'Common Stock':
            earnings_history = (res.get('Earnings') or {}).get('History') or {}
            recent_earnings = dict(list(earnings_history.items())[:4]) if isinstance(earnings_history, dict) else "N/A"
            
            earnings_trend = (res.get('Earnings') or {}).get('Trend') or {}
            forward_estimates = dict(list(earnings_trend.items())[:2]) if isinstance(earnings_trend, dict) else "N/A"
            
            cash_flow = (res.get('Financials') or {}).get('Cash_Flow') or {}
            yearly_cf = cash_flow.get('yearly') or {}
            recent_fcf = {k: v.get('freeCashFlow') for k, v in list(yearly_cf.items())[:3]} if isinstance(yearly_cf, dict) else "N/A"

            income_stmt = (res.get('Financials') or {}).get('Income_Statement') or {}
            yearly_income = income_stmt.get('yearly') or {}
            rev_5yr = {}
            rnd_5yr = {}
            for k, v in list(yearly_income.items())[:5]: 
                rev_5yr[k] = v.get('totalRevenue')
                rnd_5yr[k] = v.get('researchDevelopment')

            s = res.get('SharesStats') or {}

            packet["Stock_Specifics"] = {
                "Debt_To_Equity": h.get('NetDebtToEquity'),
                "Dividend_Yield": h.get('DividendYield'),
                "Dividend_Payout_Ratio": h.get('DividendShare'),
                "Institutional_Percent": s.get('InstitutionsPercent'),
                "Insider_Percent": s.get('InsiderPercent'),
                "Free_Cash_Flow_History_3Y": recent_fcf,             # <--- 大摩狂喜
                "Revenue_History_5Y": rev_5yr,                       # <--- 高盛狂喜
                "R&D_Expense_History_5Y": rnd_5yr,                   # <--- 贝恩/费雪狂喜
                "Earnings_Beat_Miss_Last_4Q": recent_earnings,       # <--- 小摩狂喜
                "Forward_Consensus_Estimates": forward_estimates     # <--- 小摩狂喜
            }
            
        return packet
    except Exception as e:
        return None

# ==========================================
# 4. 全明星专家 Prompt 库 (硬核原版无删减)
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
            "Goldman Sachs (高盛高级分析师)": """You are a senior equity analyst at Goldman Sachs with 20 years of experience. Analyze and provide:
- P/E ratio analysis compared to sector averages
- Revenue growth trends over the last 5 years
- Debt-to-equity health check
- Dividend yield and payout sustainability score
- Competitive moat rating (weak, moderate, strong)
- Bull case and bear case price targets for 12 months
- Risk rating on a scale of 1-10 with clear reasoning
- Entry price zones and stop-loss suggestions""",
            
            "Morgan Stanley (大摩DCF估值VP)": """You are a VP-level investment banker at Morgan Stanley. Build a full DCF perspective:
- Revenue projection with growth assumptions
- Operating margin estimates based on historical trends
- Free cash flow calculations year by year (use provided FCF data)
- Weighted average cost of capital (WACC) estimate based on Beta
- Clear verdict: undervalued, fairly valued, or overvalued
- Key assumptions that could break the model""",

            "Bridgewater (桥水资深风险官)": """You are a senior risk analyst at Bridgewater Associates. Evaluate:
- Sector concentration risk
- Interest rate sensitivity for this position (analyze Beta and Debt)
- Recession stress test showing estimated drawdown
- Liquidity risk rating
- Tail risk scenarios with probability estimates
- Hedging strategies to reduce top risks""",

            "JPMorgan (小摩财报策略师)": """You are a senior equity research analyst at JPMorgan Chase. Deliver an earnings analysis:
- Last 4 quarters earnings vs estimates (beat or miss history)
- Key metrics Wall Street is watching (Forward Consensus)
- Segment-by-segment revenue breakdown trends
- Options market implied move for earnings day
- Bull case scenario and price impact estimate
- Bear case scenario and downside risk estimate
- My recommended play: buy, sell, or wait""",

            "BlackRock (贝莱德多资产策略师)": """You are a senior portfolio strategist at BlackRock managing multi-asset portfolios. Create:
- Exact asset allocation perspective
- Core holdings vs satellite positions clearly labeled
- Expected annual return range based on historical data
- Expected maximum drawdown in a bad year
- Rebalancing schedule and trigger rules""",

            "Citadel (城堡高级量化交易员)": """You are a senior quantitative trader at Citadel who combines technical analysis with statistical models. Analyze:
- Current trend direction
- Key support and resistance levels based on 52W High/Low
- Moving average analysis (50-day, 200-day) and crossover signals
- RSI, MACD readings interpretation (Use the provided exact RSI and MACD data)
- Ideal entry price, stop-loss level, and profit target
- Risk-to-reward ratio for the current setup
- Confidence rating: strong buy, buy, neutral, sell, strong sell""",

            "Harvard (哈佛捐赠基金策略师)": """You are the chief investment strategist for Harvard's endowment fund. Build a dividend perspective:
- Dividend yield and safety score (1-10 scale)
- Payout ratio analysis to flag any unsustainable dividends
- Monthly income projection potential
- DRIP reinvestment projection showing compounding
- Ranked safety of this pick for long-term hold""",

            "Bain (贝恩资深战略合伙人)": """You are a senior partner at Bain & Company. Provide a competitive landscape report:
- Top competitors in the sector
- Revenue and profit margin comparison
- Competitive moat analysis (brand, cost, network, switching)
- Management quality rating
- Innovation pipeline and R&D spending (Use R&D Expense History)
- Biggest threats to the sector (regulation, disruption, macro)
- SWOT analysis""",

            "Renaissance (文艺复兴量化研究员)": """You are a quantitative researcher at Renaissance Technologies. Identify hidden patterns:
- Insider buying and selling patterns (use Insider_Percent)
- Institutional ownership trend
- Short interest analysis and squeeze potential (use Short_Ratio)
- Price behavior around earnings (pre-run, post-gap patterns)
- Statistical edge summary: what gives this stock a quantifiable advantage""",

            "McKinsey (麦肯锡宏观合伙人)": """You are a senior partner at McKinsey's Global Institute. Analyze macro impacts:
- Current interest rate environment and its impact on this specific asset
- Inflation trend analysis and whether it benefits or suffers
- US dollar strength impact (domestic vs international)
- Global risk factors (geopolitics, trade wars, supply chains) affecting this company"""
        }
    }

# ==========================================
# 5. AI 评估引擎 (动态视角 + 1500长文本输出)
# ==========================================
def get_ai_response(name, role_desc, data):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    asset_type = data.get('Meta', {}).get('Asset_Type', 'Unknown')
    
    try:
        prompt = f"""
        你现在是 {name}。
        这是你的核心分析框架和任务要求：
        {role_desc}
        
        【🚨极端重要指令】：当前评估的标的类型为 **{asset_type}**。
        1. 如果是【Common Stock (股票)】，请严格执行你上述的财务分析/DCF模型。
        2. 如果是【ETF / Fund (行业基金/宽基)】，请**立即转换视角**！把目标视为“一个投资组合”。基于它提供的 Top_5_Holdings、费率、股息率进行配置分析。
        3. 如果是【Commodity / ETC (商品，如黄金GLD)】，请纯粹从实际利率、避险情绪、通胀预期及技术面的角度进行战略评估。

        请基于以下最新的彭博/EODHD 真实数据包进行深度分析（如果某数据缺失，基于常识推演，绝不要抱怨缺失）：
        {json.dumps(data, ensure_ascii=False)}
        
        输出要求：
        1. 必须使用**中文**回答。请将你的英文分析框架完美翻译为中文专业研报。
        2. 利用具体数字支撑观点。排版使用 Markdown（加粗、列表）。
        3. 【绝对要求】你必须在回答的最后，另起一行，单独列出评分，格式必须精确包含“评分：X/10”。
        """
        
        resp = client.chat.completions.create(
            model="deepseek-chat",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=4000, temperature=0.65, timeout=120 
        )
        content = resp.choices[0].message.content.strip()
        
        score_match = re.search(r'评分[：:\s]*([0-9.]+)', content)
        if score_match:
            score = f"{score_match.group(1)}/10"
            main_text = re.sub(r'【?评分[：:\s]*([0-9.]+).*', '', content, flags=re.IGNORECASE|re.DOTALL).strip()
        else:
            score = "未评"
            main_text = content
            
        return name, main_text, score
    except Exception as e:
        return name, f"⚠️ 评估失败或连接超时: {str(e)}", "Error"

# ==========================================
# 6. UI 主界面渲染
# ==========================================
st.title("🏆 Super Committee AI (满血部署版)")
st.caption("22 位投资大师与华尔街专家 · 深度个股/ETF/商品全覆盖系统")
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("🔍 搜索标的 (输入代码或名称，如: AAPL, GLD, 600519)", placeholder="支持美股、A股、港股、ETF及加密货币")
    
selected_ticker = None
if search_query:
    search_results = search_ticker(search_query)
    if search_results:
        selected_ticker = st.selectbox("🎯 请确认目标标的：", search_results).split(" | ")[0]
    else:
        st.warning("未找到匹配标的，请检查拼写。")

if st.button("🚀 启动全明星深度会诊") and selected_ticker:
    with st.spinner(f"正在从 EODHD 提取 {selected_ticker} 深度财务报表与核心技术指标..."):
        rich_data = fetch_comprehensive_data(selected_ticker)
    
    if rich_data:
        st.success(f"✅ 数据提取成功！正在唤醒 22 位专家，请稍候 (预计需 10-30 秒)...")
        
        experts = get_expert_prompts()
        all_experts = {**experts["投资大师组"], **experts["投资专家组"]}
        total_experts = len(all_experts)
        
        tab_m, tab_i = st.tabs(["🌟 传奇投资大师 (12位)", "🏛️ 华尔街机构专家 (10位)"])
        
        placeholders = {}
        with tab_m:
            cols_m = st.columns(2)
            for i, name in enumerate(experts["投资大师组"].keys()):
                with cols_m[i%2]:
                    placeholders[name] = st.container(height=550, border=True)
                    placeholders[name].info(f"⏳ {name} 正在深度审阅...")
        
        with tab_i:
            cols_i = st.columns(2)
            for i, name in enumerate(experts["投资专家组"].keys()):
                with cols_i[i%2]:
                    placeholders[name] = st.container(height=550, border=True)
                    placeholders[name].info(f"⏳ {name} 正在构建报告...")

        completed = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        with ThreadPoolExecutor(max_workers=15) as executor:
            futures =[executor.submit(get_ai_response, name, desc, rich_data) for name, desc in all_experts.items()]
            
            for future in as_completed(futures):
                name, text, score = future.result()
                completed += 1
                
                progress_pct = int((completed / total_experts) * 100)
                progress_bar.progress(progress_pct)
                status_text.markdown(f"**⚡ 评估进行中: {completed}/{total_experts} 个专家已出具意见...**")
                
                with placeholders[name]:
                    placeholders[name].empty()
                    st.markdown(f"<h3 class='expert-title'>👤 {name}</h3>", unsafe_allow_html=True)
                    st.markdown(text)
                    
                    score_color = "#d32f2f" if score in ["未评", "Error"] else "#1565C0"
                    st.markdown(f"<hr style='margin: 10px 0;'><div class='score-badge' style='color:{score_color}'>🎯 评分：{score}</div>", unsafe_allow_html=True)
        
        status_text.success(f"✅ 评估完成！全部 {total_experts} 位大师与专家的意见已出具。")
        progress_bar.empty()
    else:
        st.error("数据抓取失败，可能原因：代码不正确、API额度耗尽、或该公司无可用基本面数据。")