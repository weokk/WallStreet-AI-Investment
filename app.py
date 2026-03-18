import streamlit as st
import requests
import json
import re
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ==========================================
# 1. 页面配置与 CSS 样式
# ==========================================
st.set_page_config(page_title="Super Committee AI V1", layout="wide", page_icon="🏦")

st.markdown("""
    <style>
    .expert-card { background-color: #ffffff; border: 1px solid #e0e0e0; border-radius: 10px; padding: 20px; margin-bottom: 20px; height: 550px; overflow-y: auto; box-shadow: 0 4px 6px rgba(0,0,0,0.05); }
    .score-badge { font-size: 18px; font-weight: 900; color: #d32f2f; background-color: #ffebee; padding: 5px 15px; border-radius: 5px; display: inline-block; margin-top: 15px; border: 1px solid #ffcdd2; }
    .expert-title { color: #1565C0; margin-bottom: 10px; border-bottom: 2px solid #e3f2fd; padding-bottom: 5px; }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# 2. 安全认证与云端配置读取 (Secrets)
# ==========================================
def check_password():
    if st.session_state.get("password_correct", False): return True
    st.title("🔒 访问受限")
    st.info("此应用已开启满血版数据引擎，每次查询消耗量较大，请输入访问密码。")
    password_input = st.text_input("请输入访问密码", type="password")
    
    correct_password = st.secrets.get("APP_PASSWORD", "admin")

    if st.button("登录"):
        if password_input == correct_password:
            st.session_state["password_correct"] = True
            st.rerun()
        else: st.error("密码错误")
    return False

if not check_password(): st.stop()

try:
    EODHD_API_KEY = st.secrets["EODHD_API_KEY"]
    DEEPSEEK_API_KEY = st.secrets["DEEPSEEK_API_KEY"]
    DEEPSEEK_BASE_URL = st.secrets.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com")
except Exception as e:
    st.error(f"❌ 配置文件读取失败: {e}")
    st.stop()

# ==========================================
# 3. 核心数据引擎 (智能搜索与防弹解析)
# ==========================================
def search_ticker(query):
    """增强版智能搜索：彻底修复 404 切割错误，并扩大搜索池"""
    if not query or not EODHD_API_KEY: return []
    
    query = query.upper().strip()
    results =[]
    
    # 1. 强制压入精确输入直达 (注意这里的 | 前后都有空格，格式非常干净)
    if '.' in query:
        results.append(f"{query} | [强制精确抓取]")
        
    base_query = query.split('.')[0] if '.' in query else query
    target_exchange = query.split('.')[1] if '.' in query else None
    
    # 扩大 limit 到 50，尽力捞出冷门市场同名股
    url = f"https://eodhd.com/api/search/{base_query}?api_token={EODHD_API_KEY}&limit=50&fmt=json"
    try:
        res = requests.get(url, timeout=5).json()
        if not isinstance(res, list): return results
        
        for item in res:
            asset_type = item.get('Type', 'Unknown')
            exchange = item.get('Exchange', '')
            code_str = f"{item.get('Code')}.{exchange} | {item.get('Name')} ({asset_type})"
            
            # 如果匹配到目标交易所，放到前面 (紧跟直达选项)
            if target_exchange and target_exchange == exchange:
                if len(results) > 0 and "[强制精确抓取]" in results[0]:
                    results.insert(1, code_str)
                else:
                    results.insert(0, code_str)
            else:
                results.append(code_str)
                
        # 去重
        seen = set()
        final_res =[]
        for x in results:
            if x not in seen:
                seen.add(x)
                final_res.append(x)
                
        return final_res[:20]
    except Exception as e:
        return results

def safe_dict(data, key):
    """绝对防弹的数据提取器，防止日韩股票 API 返回空列表导致崩溃"""
    if isinstance(data, dict):
        val = data.get(key)
        if isinstance(val, dict): return val
    return {}

def fetch_comprehensive_data(ticker_code):
    """满血全量数据抓取：包含防崩溃机制"""
    try:
        url_fund = f"https://eodhd.com/api/fundamentals/{ticker_code}?api_token={EODHD_API_KEY}&fmt=json"
        res_raw = requests.get(url_fund, timeout=15)
        
        if res_raw.status_code != 200:
            return None, f"HTTP Error {res_raw.status_code}: 无法在 EODHD 找到该代码 ({ticker_code}) 的基本面数据。"
            
        res = res_raw.json()
        if not isinstance(res, dict):
            return None, f"API 返回了异常的非字典格式"
            
        if 'message' in res and 'General' not in res:
            return None, f"EODHD 拦截: {res['message']}"

        g = safe_dict(res, 'General')
        h = safe_dict(res, 'Highlights')
        v = safe_dict(res, 'Valuation')
        t = safe_dict(res, 'Technicals')
        s = safe_dict(res, 'SharesStats')
        
        asset_type = g.get('Type', 'Common Stock')
        
        latest_rsi, latest_macd = "N/A", "N/A"
        if asset_type in ['Common Stock', 'ETF']:
            try:
                url_rsi = f"https://eodhd.com/api/technical/{ticker_code}?function=rsi&period=14&api_token={EODHD_API_KEY}&fmt=json"
                rsi_data = requests.get(url_rsi, timeout=5).json()
                if isinstance(rsi_data, list) and rsi_data: latest_rsi = round(rsi_data[-1].get('rsi', 0), 2)
                
                url_macd = f"https://eodhd.com/api/technical/{ticker_code}?function=macd&api_token={EODHD_API_KEY}&fmt=json"
                macd_data = requests.get(url_macd, timeout=5).json()
                if isinstance(macd_data, list) and macd_data: 
                    latest_macd = {"MACD_Value": round(macd_data[-1].get('macd', 0), 4), "Signal_Line": round(macd_data[-1].get('signal', 0), 4)}
            except: pass

        packet = {
            "Meta": {
                "Asset_Type": asset_type,
                "Currency": g.get('CurrencyCode', 'USD'),
                "Sector": g.get('Sector'), "Industry": g.get('Industry'),
                "Description": str(g.get('Description', ''))[:400]
            },
            "Valuation_&_Profitability": {
                "PE": v.get('TrailingPE'), "Forward_PE": v.get('ForwardPE'),
                "ROE": h.get('ReturnOnEquityTTM'), "Operating_Margin": h.get('OperatingMarginTTM')
            },
            "Technicals_&_Risk": {
                "Beta": t.get('Beta'), "50_Day_MA": t.get('50DayMA'), "200_Day_MA": t.get('200DayMA'),
                "Short_Ratio": t.get('ShortRatio'), "RSI_14Day": latest_rsi, "MACD_Latest": latest_macd
            }
        }

        if asset_type in ['ETF', 'Fund', 'Mutual Fund']:
            etf_data = safe_dict(res, 'ETF_Data')
            top_10 = safe_dict(etf_data, 'Top_10_Holdings')
            packet["ETF_Specifics"] = {
                "Expense_Ratio": etf_data.get('NetExpenseRatio'),
                "Yield": etf_data.get('Yield'),
                "Top_5_Holdings": {k: safe_dict(top_10, k).get('Assets_%') for k in list(top_10.keys())[:5]} if top_10 else "N/A"
            }
            
        elif asset_type == 'Common Stock':
            earnings = safe_dict(res, 'Earnings')
            earnings_hist = safe_dict(earnings, 'History')
            recent_earnings = dict(list(earnings_hist.items())[:4]) if earnings_hist else "N/A"
            
            earnings_trend = safe_dict(earnings, 'Trend')
            fwd_estimates = dict(list(earnings_trend.items())[:2]) if earnings_trend else "N/A"
            
            financials = safe_dict(res, 'Financials')
            cash_flow = safe_dict(financials, 'Cash_Flow')
            yearly_cf = safe_dict(cash_flow, 'yearly')
            recent_fcf = {k: safe_dict(yearly_cf, k).get('freeCashFlow') for k in list(yearly_cf.keys())[:3]} if yearly_cf else "N/A"

            income_stmt = safe_dict(financials, 'Income_Statement')
            yearly_inc = safe_dict(income_stmt, 'yearly')
            rev_5yr, rnd_5yr = {}, {}
            for k in list(yearly_inc.keys())[:5]:
                rev_5yr[k] = safe_dict(yearly_inc, k).get('totalRevenue')
                rnd_5yr[k] = safe_dict(yearly_inc, k).get('researchDevelopment')

            packet["Stock_Specifics"] = {
                "Debt_To_Equity": h.get('NetDebtToEquity'),
                "Dividend_Yield": h.get('DividendYield'),
                "Institutional_Percent": s.get('InstitutionsPercent'),
                "Free_Cash_Flow_History_3Y": recent_fcf,             
                "Revenue_History_5Y": rev_5yr,                       
                "R&D_Expense_History_5Y": rnd_5yr,                   
                "Earnings_Beat_Miss_Last_4Q": recent_earnings,       
                "Forward_Consensus_Estimates": fwd_estimates     
            }
            
        return packet, None
    except Exception as e:
        return None, traceback.format_exc()

# ==========================================
# 4. 全明星专家 Prompt 库
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

# ==========================================
# 5. AI 评估引擎 (动态视角 + 1500长文本输出)
# ==========================================
def get_ai_response(name, role_desc, data):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    asset_type = data.get('Meta', {}).get('Asset_Type', 'Unknown')
    currency = data.get('Meta', {}).get('Currency', 'USD')
    
    try:
        prompt = f"""
        你现在是 {name}。
        分析框架与任务：{role_desc}
        
        【🚨极其重要指令】：当前资产类型为 **{asset_type}**，计价货币为 **{currency}**。
        1. 如果是【Common Stock (股票)】，严格执行财务分析/DCF模型，涉及金额时必须带入该国汇率和宏观常识。
        2. 如果是【ETF / Fund (基金)】，立即转换视角为“投资组合配置”！基于其重仓股(Top Holdings)及费率分析，拒绝计算单只股票的护城河/现金流。
        3. 如果是【Commodity / ETC (商品，如GLD)】，纯粹从实际利率、避险情绪及技术面进行宏观战略评估，忽略股权指标。

        最新数据包（缺失数据请常识推演，绝不报错）：
        {json.dumps(data, ensure_ascii=False)}
        
        输出规则：
        1. **语言**：中文。将英文框架完美翻译为中文研报。
        2. **论据**：利用具体数字支撑观点。排版使用 Markdown。
        3. **【绝对要求】**：在所有分析结束后，必须另起一行，以纯文本格式输出评分，例如：“评分：8.5/10”。
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
        return name, f"⚠️ 评估失败: {str(e)}", "Error"

# ==========================================
# 6. UI 主界面渲染
# ==========================================
st.title("🏆 Super Committee AI (满血部署版)")
st.caption("22 位投资大师与华尔街专家 · 深度个股/ETF/商品全覆盖系统")
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("🔍 搜索全球标的 (输入代码或名称，如: AAPL, 7203.T, 1605.T, 005930.KO)", placeholder="支持美股、日韩股、A股、港股及加密货币")
    
selected_ticker = None
if search_query:
    search_results = search_ticker(search_query)
    if search_results:
        choice = st.selectbox("🎯 请确认目标标的：", search_results)
        # 【核心修复】：最暴力、最安全的字符串切割，确保 API 只收到纯净的代码
        selected_ticker = choice.split("|")[0].strip()
    else:
        st.warning("未找到匹配标的，请检查拼写。提示：日股请加 .T，韩股请加 .KO")

if st.button("🚀 启动全明星深度会诊") and selected_ticker:
    with st.spinner(f"正在从 EODHD 提取 {selected_ticker} 深度财务报表与核心技术指标..."):
        rich_data, error_log = fetch_comprehensive_data(selected_ticker)
    
    if rich_data:
        currency = rich_data.get('Meta', {}).get('Currency', 'USD')
        st.success(f"✅ 数据提取成功！(货币: **{currency}**) 正在唤醒 22 位专家，请稍候...")
        
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
        st.error(f"❌ 数据抓取失败！\n\n**开发者诊断日志:**\n```\n{error_log}\n```\n请检查：1.代码是否正确 2.您的 EODHD 套餐是否包含该国家/资产的数据权限。")
