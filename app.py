import streamlit as st
import requests
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from openai import OpenAI

# ==========================================
# 1. 页面配置与 CSS 样式
# ==========================================
st.set_page_config(page_title="Super Committee AI (Max Data + Day1)", layout="wide", page_icon="🏦")

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
    st.info("此应用已开启满血版数据引擎及 Day1 深度框架，每次查询消耗量较大，请输入访问密码。")
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
# 3. 满血版核心数据引擎 (含技术指标与Day1数据)
# ==========================================
def search_ticker(query):
    """增强版模糊搜索与精确直达"""
    if not query or not EODHD_API_KEY: return []
    
    results =[]
    
    # 1. 允许精确直连 (Override)：如果用户输入了带后缀的代码（如 7203.T）
    # 直接作为第一选项强制压入。哪怕搜索 API 没搜到，也能直接抓取数据！
    if '.' in query:
        clean_query = query.upper().strip()
        results.append(f"{clean_query} | [精确输入直达/Direct Override]")
        
    # 2. 优化搜索词：EODHD 对带点号的搜索支持不好
    # 我们自动剥离后缀（如 1605.t -> 1605），让它返回全球所有叫 1605 的标的
    base_query = query.split('.')[0] if '.' in query else query
    base_query = base_query.strip()
    
    url = f"https://eodhd.com/api/search/{base_query}?api_token={EODHD_API_KEY}&fmt=json"
    try:
        res = requests.get(url, timeout=5).json()
        
        # 3. 扩大展示数量到 15 个，防止需要的市场代码被折叠
        for item in res[:15]:
            # 兼容处理有时 API 缺少 Type 字段的情况
            asset_type = item.get('Type', 'Unknown')
            code_str = f"{item['Code']}.{item['Exchange']} | {item['Name']} ({asset_type})"
            
            # 去重：防止 API 搜到的和我们强制压入的第一项重复
            if code_str not in results:
                results.append(code_str)
                
    except Exception as e:
        print(f"搜索 API 异常: {e}")
        pass
        
    return results
def fetch_latest_news(ticker_code):
    """【新增】抓取最新5条新闻作为 Day1 的催化剂补充"""
    url = f"https://eodhd.com/api/news?s={ticker_code}&api_token={EODHD_API_KEY}&limit=5&fmt=json"
    try:
        res = requests.get(url, timeout=5).json()
        return[{"Date": item['date'][:10], "Title": item['title']} for item in res]
    except: return[]

def fetch_comprehensive_data(ticker_code):
    """满血全量数据抓取：包含基础面、历史财报、技术指标与新闻"""
    try:
        url_fund = f"https://eodhd.com/api/fundamentals/{ticker_code}?api_token={EODHD_API_KEY}&fmt=json"
        res = requests.get(url_fund, timeout=15).json()
        
        g = res.get('General') or {}
        h = res.get('Highlights') or {}
        v = res.get('Valuation') or {}
        t = res.get('Technicals') or {}
        
        asset_type = g.get('Type', 'Common Stock')
        currency = g.get('CurrencyCode', 'USD') # 【新增】全局货币修正
        
        # 【新增】计算 Rule of 40 (营收增长率 + 营业利润率)
        rev_growth = h.get('RevenueGrowthYoY') or 0
        op_margin = h.get('OperatingMarginTTM') or 0
        rule_of_40 = round((rev_growth + op_margin) * 100, 2)

        # 获取技术指标 (RSI / MACD)
        latest_rsi, latest_macd = "N/A", "N/A"
        if asset_type in['Common Stock', 'ETF']:
            try:
                url_rsi = f"https://eodhd.com/api/technical/{ticker_code}?function=rsi&period=14&api_token={EODHD_API_KEY}&fmt=json"
                rsi_data = requests.get(url_rsi, timeout=5).json()
                if rsi_data and isinstance(rsi_data, list): latest_rsi = round(rsi_data[-1].get('rsi', 0), 2)
                
                url_macd = f"https://eodhd.com/api/technical/{ticker_code}?function=macd&api_token={EODHD_API_KEY}&fmt=json"
                macd_data = requests.get(url_macd, timeout=5).json()
                if macd_data and isinstance(macd_data, list): 
                    latest_macd = {
                        "MACD_Value": round(macd_data[-1].get('macd', 0), 4),
                        "Signal_Line": round(macd_data[-1].get('signal', 0), 4)
                    }
            except: pass

        packet = {
            "Meta": {
                "Asset_Type": asset_type,
                "Currency": currency,  # <--- 加入货币单位
                "Sector": g.get('Sector'), 
                "Industry": g.get('Industry'),
                "Description": g.get('Description', '')[:400]
            },
            "Valuation_&_Profitability": {
                "PE": v.get('TrailingPE'), "Forward_PE": v.get('ForwardPE'),
                "PB": v.get('PriceBookMRQ'), "EV_EBITDA": v.get('EnterpriseValueEbitda'), # <--- Day1 需要
                "ROE": h.get('ReturnOnEquityTTM'), "Operating_Margin": op_margin,
                "Rule_of_40_Score": rule_of_40  # <--- Day1 科技股分析需要
            },
            "Technicals_&_Risk": {
                "Beta": t.get('Beta'), "50_Day_MA": t.get('50DayMA'), 
                "200_Day_MA": t.get('200DayMA'), "52W_High": t.get('52WeekHigh'),
                "Short_Ratio": t.get('ShortRatio'),
                "RSI_14Day": latest_rsi,       
                "MACD_Latest": latest_macd     
            }
        }

        if asset_type in['ETF', 'Fund', 'Mutual Fund']:
            etf_data = res.get('ETF_Data') or {}
            top_10 = etf_data.get('Top_10_Holdings') or {}
            packet["ETF_Specifics"] = {
                "Expense_Ratio": etf_data.get('NetExpenseRatio'),
                "Yield": etf_data.get('Yield'),
                "Top_5_Holdings": {k: v.get('Assets_%') for k, v in list(top_10.items())[:5]} if top_10 else "N/A"
            }
            
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
            rev_5yr, rnd_5yr = {}, {}
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
                "Free_Cash_Flow_History_3Y": recent_fcf,             
                "Revenue_History_5Y": rev_5yr,                       
                "R&D_Expense_History_5Y": rnd_5yr,                   
                "Earnings_Beat_Miss_Last_4Q": recent_earnings,       
                "Forward_Consensus_Estimates": forward_estimates     
            }
            # 【新增】抓取新闻给 Day1 做催化剂
            packet["Recent_News_Catalysts"] = fetch_latest_news(ticker_code)
            
        return packet
    except Exception as e:
        return None

# ==========================================
# 4. 全明星与 Day1 Prompt 库
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
            "Goldman Sachs (高盛高级分析师)": "You are a senior equity analyst at Goldman Sachs. Analyze: P/E compared to sector, Revenue growth trend, Debt-to-equity, Dividend sustainability, Competitive moat rating, Bull/Bear case price targets, Risk rating (1-10), Entry/Stop-loss zones.",
            "Morgan Stanley (大摩DCF估值VP)": "You are a VP at Morgan Stanley. Build a DCF perspective: Evaluate operating margins, Free cash flow calculations year by year, WACC implications (using Beta), and give a clear verdict.",
            "Bridgewater (桥水资深风险官)": "You are a senior risk analyst at Bridgewater. Evaluate: Sector concentration risk, Interest rate sensitivity, Recession stress test (drawdown estimate), Liquidity risk, Single stock risk, and Hedging strategies.",
            "JPMorgan (小摩财报策略师)": "You are a senior equity research analyst at JPMorgan. Deliver an earnings analysis: Last 4 quarters earnings vs estimates, Consensus estimates, Options implied move, Bull/Bear case price impact.",
            "BlackRock (贝莱德配置)": "You are a senior portfolio strategist at BlackRock. Create an allocation plan: Core vs satellite positions, Expected annual return range, Expected maximum drawdown, Rebalancing schedule.",
            "Citadel (城堡量化交易员)": "You are a quantitative trader at Citadel. Analyze: Trend direction, Support/resistance, Moving average (50/200), RSI/MACD readings, Ideal entry price/stop-loss, Risk-to-reward ratio.",
            "Harvard (哈佛分红策略)": "You are the chief investment strategist for Harvard's endowment. Build a dividend perspective: Dividend yield, safety score (1-10), Payout ratio analysis, DRIP reinvestment projection.",
            "Bain (贝恩资深战略合伙人)": "You are a senior partner at Bain & Company. Provide a competitive landscape report: Margin comparison, Moat analysis, Innovation pipeline (R&D), Biggest threats, SWOT analysis.",
            "Renaissance (文艺复兴量化)": "You are a quant at Renaissance Technologies. Identify patterns: Insider buying/selling, Institutional ownership, Short interest and squeeze potential, Price behavior around earnings.",
            "McKinsey (麦肯锡宏观)": "You are a senior partner at McKinsey. Analyze macro impacts: Current interest rate environment, Inflation trend, US dollar strength impact, Global risk factors."
        }
    }

def get_day1_modules():
    return {
        "🧭 模块 1：核心基本面与新闻催化剂 (Fundamentals & Catalyst)": "系统性分析其商业模式、营收质量（通过现金流印证）。结合数据包中最新的 News_Catalysts（新闻事件），判断近 6 个月内可能引发股价重估的催化剂是什么？",
        "🧮 模块 2：多维估值矩阵 (Valuation Matrix)": "综合评估其 EV/EBITDA 倍数、Rule of 40（营收增长+利润率）健康度、自由现金流。判断当前市场定价是计入了悲观预期还是透支了未来？",
        "⚖️ 模块 3：投资哲学交叉验证 (Cross-Philosophies)": "用三大哲学交叉审视：1.老虎基金视角（基本面做多/做空的逻辑是什么？） 2. 橡树资本视角（当前价格具备足够的安全边际吗？） 3. 德鲁肯米勒视角（宏观流动性与行业趋势是顺风还是逆风？）",
        "🚨 模块 4：事前尸检与行动计划 (Pre-Mortem & Action Plan)": "【反偏见核心排雷】请执行‘事前尸检’：假设该笔投资在 2 年后亏损了 50%，倒推最可能导致暴跌的 3 个致命原因。最后，给出极其明确的建仓策略（如观察哪些红旗指标，在什么支撑位建仓）。"
    }

# ==========================================
# 5. AI 评估引擎 (支持动态视角与货币校准)
# ==========================================
def get_ai_response(name, role_desc, data, is_day1=False):
    client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    
    asset_type = data.get('Meta', {}).get('Asset_Type', 'Unknown')
    currency = data.get('Meta', {}).get('Currency', 'USD')
    
    # 货币单位校准提示
    currency_prompt = f"【🚨极其重要】：该标的财务数据计价货币为 **{currency}**。请在分析绝对金额时，务必结合该国货币单位及当地宏观背景（如日本/韩国的汇率及通胀环境），避免将其当成美元产生估值幻觉。"
    
    try:
        if not is_day1:
            prompt = f"""
            你现在是 {name}。任务要求：{role_desc}
            
            当前标的类型为 **{asset_type}**。{currency_prompt}
            1. 若是【股票】，请严格执行上述财务/DCF分析。
            2. 若是【ETF/基金】，请转换视角为投资组合配置（基于Top Holdings和费率分析）。
            3. 若是【商品(如GLD)】，请忽略股权指标，纯粹从实际利率、避险及技术面评估。

            请基于最新的彭博/EODHD数据进行分析：
            {json.dumps(data, ensure_ascii=False)}
            
            要求：
            1. 必须使用**中文**回答。利用具体数字支撑观点。
            2. 排版使用 Markdown。
            3. 【绝对要求】在回答的最后另起一行，单独列出评分，格式必须精确包含“评分：X/10”。
            """
        else:
            prompt = f"""
            你正在执行顶级对冲基金的【Day1 Global 深度投研框架】模块：{name}。
            核心指令：{role_desc}
            
            当前标的类型为 **{asset_type}**。{currency_prompt}
            
            请基于全量数据（包含近期新闻、Rule of 40 等）：
            {json.dumps(data, ensure_ascii=False)}
            
            要求：
            1. 必须使用**中文**回答。直接输出结构化的高密度研报，拒绝废话。
            2. 极度客观。大量使用数据包中的真实数字（如 EV/EBITDA, 新闻事件）作为论据。
            3. 使用 Markdown 使得层级分明。
            （Day1模块不需要给出最终评分）
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
# 6. UI 主界面渲染
# ==========================================
st.title("🏆 Super Committee AI (Day1 Global 版)")
st.caption("集成了 22 位大师研报与 Day1 Global 深度反偏见框架的全资产系统")
st.markdown("---")

col1, col2 = st.columns([3, 1])
with col1:
    search_query = st.text_input("🔍 搜索全球标的 (如: NVDA, 7203.T (丰田), 005930.KO (三星))", placeholder="支持美股、日韩、A/港股及ETF")
    
selected_ticker = None
if search_query:
    search_results = search_ticker(search_query)
    if search_results:
        selected_ticker = st.selectbox("🎯 请确认目标标的：", search_results).split(" | ")[0]
    else:
        st.warning("未找到匹配标的，请检查拼写或尝试添加市场后缀 (如 .T, .KO)")

if st.button("🚀 启动深度扫描") and selected_ticker:
    with st.spinner(f"正在从 EODHD 提取 {selected_ticker} 深度财务、筹码与新闻数据..."):
        rich_data = fetch_comprehensive_data(selected_ticker)
    
    if rich_data:
        currency = rich_data.get('Meta', {}).get('Currency', 'USD')
        st.success(f"✅ 数据提取成功！(计价货币: **{currency}**)。正在唤醒全网模型...")
        
        experts = get_expert_prompts()
        day1_modules = get_day1_modules()
        total_tasks = len(experts["投资大师组"]) + len(experts["投资专家组"]) + len(day1_modules)
        
        tab_m, tab_i, tab_day1 = st.tabs(["🌟 传奇大师意见 (12位)", "🏛️ 机构专家评估 (10位)", "🌐 Day1 深度投研 (反偏见)"])
        
        placeholders = {}
        
        # 布局分配
        with tab_m:
            cols_m = st.columns(2)
            for i, name in enumerate(experts["投资大师组"].keys()):
                with cols_m[i%2]:
                    placeholders[name] = {"ui": st.container(height=500, border=True), "is_day1": False}
                    placeholders[name]["ui"].info(f"⏳ {name} 正在深度审阅...")
        
        with tab_i:
            cols_i = st.columns(2)
            for i, name in enumerate(experts["投资专家组"].keys()):
                with cols_i[i%2]:
                    placeholders[name] = {"ui": st.container(height=500, border=True), "is_day1": False}
                    placeholders[name]["ui"].info(f"⏳ {name} 正在构建报告...")
                    
        with tab_day1:
            st.info("💡 **Day1 Global 投研框架** 专注于基本面重构、估值交叉验证与“事前尸检”防雷，为严肃决策提供支持。")
            for name in day1_modules.keys():
                placeholders[name] = {"ui": st.container(border=True), "is_day1": True}
                placeholders[name]["ui"].info(f"⏳ {name} 正在计算并生成...")

        completed = 0
        progress_bar = st.progress(0)
        status_text = st.empty()
        
        # 并发执行所有 26 个任务
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
                        color = "#d32f2f" if score in ["未评", "Error"] else "#1565C0"
                        st.markdown(f"<hr style='margin:10px 0;'><div class='score-badge' style='color:{color}'>🎯 评分：{score}</div>", unsafe_allow_html=True)
        
        status_text.success(f"✅ 全盘扫描完成！请在上方切换标签页查阅报告。")
        progress_bar.empty()
    else:
        st.error("数据抓取失败，请检查 API 额度或标的代码。")
