import streamlit as st
import yfinance as yf
import pandas as pd
import plotly.express as px

# --- Set up the page ---
# Use st.set_page_config() as the first Streamlit command
st.set_page_config(page_title="Momentum Stock Tracker", layout="wide")

# --- Page Title ---
st.title("🚀 Momentum Live Stock Tracker")
st.write("Enter a stock ticker to see its live data and performance.")

# --- Sidebar for User Input ---
st.sidebar.header("User Input")
ticker_symbol = st.sidebar.text_input("Enter Stock Ticker (e.g., AAPL, GOOGL, MSFT)", "AAPL").upper()

# --- Main App Logic ---
if ticker_symbol:
    try:
        # Get the ticker data from yfinance
        ticker_data = yf.Ticker(ticker_symbol)

        # Get the stock info
        stock_info = ticker_data.info

        # --- Display Header & Key Info ---
        # Fallback to the symbol if the longName is missing
        name = stock_info.get('longName', ticker_symbol)
        st.subheader(f"{name} ({stock_info.get('symbol', ticker_symbol)})")

        # Safely get key metrics. Use None as default, as 'N/A' as a string causes formatting errors.
        current_price = stock_info.get('currentPrice')
        previous_close = stock_info.get('previousClose')
        market_cap = stock_info.get('marketCap')

        # --- Calculate Delta Safely and Prepare Display Strings ---
        price_delta = None
        price_str = "N/A"
        prev_close_str = "N/A"
        
        try:
            current = None
            prev = None
            
            # Attempt to convert to float for calculation/formatting
            if current_price is not None:
                current = float(current_price)
                price_str = f"${current:,.2f}"
            
            if previous_close is not None:
                prev = float(previous_close)
                prev_close_str = f"${prev:,.2f}"
            
            # Calculate delta only if both values are valid numbers
            if current is not None and prev is not None:
                price_delta = round(current - prev, 2)
        
        except (TypeError, ValueError):
            # If any data is malformed, delta calculation fails gracefully
            price_delta = None

        # Format delta string
        delta_str = f"{price_delta:,.2f}" if price_delta is not None else None

        # --- Display Key Info in Columns ---
        col1, col2, col3 = st.columns(3)

        # 1. Current Price
        col1.metric("Current Price", price_str, delta_str)

        # 2. Previous Close
        col2.metric("Previous Close", prev_close_str)

        # 3. Market Cap
        if market_cap is not None:
            # Use 0 decimal places for large numbers like Market Cap
            cap_str = f"${market_cap:,.0f}"
        else:
            cap_str = "N/A"

        col3.metric("Market Cap", cap_str)


        # --- Charting Section ---
        st.subheader("Historical Performance")
        
        # Period selection
        period = st.select_slider(
            "Select Time Period",
            options=["1mo", "3mo", "6mo", "1y", "2y", "5y", "max"],
            value="1y"
        )

        # Get historical data
        hist_data = ticker_data.history(period=period)

        if not hist_data.empty:
            # Create an interactive plot with Plotly
            fig = px.line(hist_data, x=hist_data.index, y="Close", 
                          title=f"{ticker_symbol} Closing Price ({period})")
            
            # Customize the plot
            fig.update_layout(
                xaxis_title="Date",
                yaxis_title="Stock Price (USD)",
                yaxis_tickprefix="$" # The correct parameter is yaxis_tickprefix, not yaxis_prefix
            )
            
            # Display the plot in Streamlit
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.warning("No historical data found for this period.")

        # --- Display More Company Info ---
        with st.expander("See Company Business Summary"):
            st.write(stock_info.get('longBusinessSummary', 'No summary available.'))

    except Exception as e:
        # Catch and display specific errors, including connection issues or invalid tickers
        st.error(f"Error: Could not retrieve data for {ticker_symbol}. Please check the ticker symbol or connection.")
        # Optionally, you can comment out the detail line for a cleaner UI once debugging is done
        # st.error(f"Details: {e}")

else:
    st.info("Please enter a stock ticker in the sidebar to get started.")