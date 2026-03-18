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
# 3. 核心引擎：强化搜索与防断裂解析
# ==========================================
def search_ticker(query):
    """超级搜索：不仅搜索代码，还展示交易所全名，并自动去重"""
    if not query or not EODHD_API_KEY: return []
    
    query = query.upper().strip()
    # 自动处理带点号的输入，如 1605.T -> 搜索 1605
    base_query = query.split('.')[0]
    
    url = f"https://eodhd.com/api/search/{base_query}?api_token={EODHD_API_KEY}&limit=50&fmt=json"
    results = []
    
    # 强制直达项
    if '.' in query:
        results.append(f"{query} | [强制精确尝试/Direct Try]")

    try:
        res = requests.get(url, timeout=5).json()
        if isinstance(res, list):
            for item in res:
                code = item.get('Code', '')
                exchange = item.get('Exchange', '')
                name = item.get('Name', '')
                asset_type = item.get('Type', 'Unknown')
                # 组合显示：代码.交易所 | 公司名 (交易所全名)
                display_str = f"{code}.{exchange} | {name} ({exchange})"
                if display_str not in results:
                    results.append(display_str)
        return results[:30] # 返回更多结果供选择
    except:
        return results

def safe_dict(data, key):
    if isinstance(data, dict):
        val = data.get(key)
        if isinstance(val, dict): return val
    return {}

def fetch_comprehensive_data(ticker_code):
    """满血版抓取：增加备用后缀重试机制与详细权限报错"""
    # 备用后缀映射
    backup_suffixes = {".T": ".TSE", ".TSE": ".T"}
    
    def try_fetch(code):
        url = f"https://eodhd.com/api/fundamentals/{code}?api_token={EODHD_API_KEY}&fmt=json"
        return requests.get(url, timeout=15)

    res_raw = try_fetch(ticker_code)
    
    # 如果 404，尝试备用后缀
    if res_raw.status_code == 404:
        for old, new in backup_suffixes.items():
            if ticker_code.endswith(old):
                new_code = ticker_code.replace(old, new)
                res_raw = try_fetch(new_code)
                if res_raw.status_code == 200:
                    ticker_code = new_code
                    break

    if res_raw.status_code != 200:
        error_msg = f"HTTP {res_raw.status_code}: "
        if res_raw.status_code == 404:
            error_msg += f"在 EODHD 库中找不到代码 {ticker_code}。这通常意味着您的 API 套餐不支持该国家市场（如日本或台湾）。"
        elif res_raw.status_code == 403:
            error_msg += "API Key 权限受限或额度已耗尽。"
        return None, error_msg

    try:
        res = res_raw.json()
        # 后续解析逻辑 (保持 safe_dict 容错)
        g = safe_dict(res, 'General')
        h = safe_dict(res, 'Highlights')
        v = safe_dict(res, 'Valuation')
        t = safe_dict(res, 'Technicals')
        s = safe_dict(res, 'SharesStats')
        
        asset_type = g.get('Type', 'Common Stock')
        
        # 技术指标
        latest_rsi, latest_macd = "N/A", "N/A"
        if asset_type in ['Common Stock', 'ETF']:
            try:
                rsi_data = requests.get(f"https://eodhd.com/api/technical/{ticker_code}?function=rsi&api_token={EODHD_API_KEY}&fmt=json").json()
                if rsi_data: latest_rsi = rsi_data[-1].get('rsi', "N/A")
                macd_data = requests.get(f"https://eodhd.com/api/technical/{ticker_code}?function=macd&api_token={EODHD_API_KEY}&fmt=json").json()
                if macd_data: latest_macd = macd_data[-1]
            except: pass

        packet = {
            "Meta": {"Asset_Type": asset_type, "Currency": g.get('CurrencyCode', 'USD'), "Name": g.get('Name'), "Sector": g.get('Sector'), "Desc": str(g.get('Description', ''))[:400]},
            "Valuation": {"PE": v.get('TrailingPE'), "ROE": h.get('ReturnOnEquityTTM')},
            "Technicals": {"Beta": t.get('Beta'), "RSI": latest_rsi, "MACD": latest_macd},
            "Stock_Specifics": {
                "Revenue_History_5Y": {k: v.get('totalRevenue') for k, v in list(safe_dict(safe_dict(res, 'Financials'), 'Income_Statement').get('yearly', {}).items())[:5]},
                "Free_Cash_Flow_History_3Y": {k: v.get('freeCashFlow') for k, v in list(safe_dict(safe_dict(res, 'Financials'), 'Cash_Flow').get('yearly', {}).items())[:3]},
            }
        }
        return packet, None
    except Exception:
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
