import pandas as pd
import numpy as np
import os

def process_dataframe(df):
  
    df['距離'] = df['距離'].astype(str)
    df['指数順位'] = df['指数順位'].astype(int)

    # 1. Replace values in '重量種別' column
    df['重量種別'] = df['重量種別'].replace(['別定', 'ハンデ'], '別ハ')

    # 2. Replace values in 'クラス名' column
    class_name_replacements = {
        '500万': '1・2勝',
        '1000万': '1・2勝',
        '1勝': '1・2勝',
        '2勝': '1・2勝',
        '1600万': '準OP以上',
        '3勝': '準OP以上',
        'ｵｰﾌﾟﾝ': '準OP以上',
        'OP(L)': '準OP以上',
        'Ｇ２': '準OP以上',
        'Ｇ３': '準OP以上'
    }
    df['クラス名'] = df['クラス名'].replace(class_name_replacements)

    # 3. Replace combinations of '重量種別' and 'クラス名' columns
    df.loc[(df['重量種別'] == '馬齢') & (df['クラス名'].isin(['1・2勝', '準OP以上'])), 'クラス名'] = '1勝以上(馬齢)'
    df.loc[(df['重量種別'] == '別ハ') & (df['クラス名'].isin(['1・2勝', '準OP以上'])), 'クラス名'] = '1勝以上(別ハ)'

    # 4. Replace values in '頭数' column
    head_count_replacements = {
        '6': '少頭数',
        '7': '少頭数',
        '8': '少頭数',
        '9': '少頭数',
        '10': '少頭数',
        '11': '少頭数',
        '12': '多頭数',
        '13': '多頭数',
        '14': '多頭数',
        '15': '多頭数',
        '16': '多頭数',
        '17': '多頭数',
        '18': '多頭数'
    }
    df['頭数'] = df['頭数'].astype(str).replace(head_count_replacements)

    # 5. Replace values in '馬場状態' column
    df['馬場状態'] = df['馬場状態'].replace(['稍', '重', '不'], '道悪')

    # 6. Replace combinations of '芝・ダ' and '距離' columns
    replacements = {
        '札幌': [('芝', '1500', '15-18'), ('芝', '1800', '15-18')],
        '福島': [('芝', '1700', '17-18'), ('芝', '1800', '17-18')],
        '新潟': [('内', '芝', '1200', '12-14'), ('内', '芝', '1400', '12-14'), 
                ('外', '芝', '1600', '16-18'), ('外', '芝', '1800', '16-18'),
                ('内', '芝', '2000', '20-24'), ('内', '芝', '2200', '20-24'), ('内', '芝', '2400', '20-24'),
                ('外', '芝', '3000', '30-32'), ('外', '芝', '3200', '30-32')],
        '中山': [('ダ', '2400', '24-25'), ('ダ', '2500', '24-25')],
        '東京': [('芝', '1600', '16-18'), ('芝', '1800', '16-18'),
               ('芝', '2300', '23-24'), ('芝', '2400', '23-24'),
               ('ダ', '1300', '13-14'), ('ダ', '1400', '13-14')],
        '中京': [('芝', '1200', '12-14'), ('芝', '1400', '12-14'),
               ('ダ', '1800', '18-19'), ('ダ', '1900', '18-19')],
        '京都': [('内', '芝', '1400', '14-16'), ('内', '芝', '1600', '14-16'),
                ('外', '芝', '1400', '14-18'), ('外', '芝', '1600', '14-18'), ('外', '芝', '1800', '14-18'),
                ('内', '芝', '2000', '20-24'), ('外', '芝', '2000', '20-24'),
                ('外', '芝', '2200', '22-24'), ('外', '芝', '2400', '22-24'),
                ('外', '芝', '3000', '30-32'), ('外', '芝', '3200', '30-32'),
                ('ダ', '1800', '18-19'), ('ダ', '1900', '18-19')],
        '阪神': [('芝', '1600', '16-18'), ('芝', '1800', '16-18'),
               ('芝', '2400', '24-26'), ('芝', '2600', '24-26'),
               ('芝', '3000', '30-32'), ('芝', '3200', '30-32')],
        '小倉': [('芝', '1700', '17-18'), ('芝', '1800', '17-18')]
    }
    
    for place, rules in replacements.items():
        for rule in rules:
            if len(rule) == 3:
                turf, distance, new_distance = rule
                df.loc[(df['場所'] == place) & (df['芝・ダ'] == turf) & (df['距離'] == distance), '距離'] = new_distance
            elif len(rule) == 4:
                inner_outer, turf, distance, new_distance = rule
                # df.loc[(df['場所'] == place) & (df['芝(内外)'] == inner_outer) & (df['芝・ダ'] == turf) & (df['距離'] == distance), '距離'] = new_distance
                df.loc[(df['場所'] == place) & (df['芝(内・外)'] == inner_outer) & (df['芝・ダ'] == turf) & (df['距離'] == distance), '距離'] = new_distance


    # 7. Replace values in 'コース区分' column
    df['コース区分'] = df['コース区分'].replace(['A', 'B', 'C', 'D'], '')

    # 8. Replace values in '枠番' column
    gate_count_replacements = {
        '1': '内枠',
        '2': '内枠',
        '3': '内枠',
        '4': '内枠',
        '5': '外枠',
        '6': '外枠',
        '7': '外枠',
        '8': '外枠'
    }
    df['枠番'] = df['枠番'].astype(str).replace(gate_count_replacements)

    # 9. Remove spaces from the DataFrame
    df = df.apply(lambda col: col.str.replace('　', '').replace(' ', '') if col.dtype == 'O' else col)

    # Convert all columns to string type and replace NaN with empty string
    df = df.astype(str).fillna("")
    # Remove columns that are entirely empty
    df = df.replace("", np.nan).dropna(axis=1, how="all").replace(np.nan, "")
    
    # Join columns with '×', replace '××' with '×', and remove trailing '×'
    df['結合データ'] = df.drop(columns="レースID(新)").astype(str).apply(lambda x: '×'.join(x), axis=1).str.replace('××', '×').str.rstrip('×')
    
    return df[["レースID(新)", "結合データ"]]


def post_process_dataframe(df: pd.DataFrame) -> pd.DataFrame:
    
    non_empty_rows = ~((df.iloc[:, -2].isna()) & (df.iloc[:, -1].isna()))
    df = df[non_empty_rows]
    
    # Replacing NaN with empty strings
    df = df.fillna("")
    
    # Remove '××' and replace with '×', and trim trailing '×' from the data
    df = df.apply(lambda col: col.str.replace('　', '').replace(' ', '').replace(' 内', '内').replace(' 外', '外') if col.dtype == "object" else col)
    
    return df


def main():
    directory = 'csv_files'
    CSV_DIR = "./replace_csv"
    for filename in os.listdir(directory):
        if filename.endswith('.csv'):
            file_path = os.path.join(directory, filename)
            df = pd.read_csv(file_path, encoding='shift_jis')
            processed_df = post_process_dataframe(df)
            final_df = process_dataframe(processed_df)

            # 2. Define output_file correctly
            output_filepath = os.path.join(CSV_DIR, f"processed_{filename}")
            final_df.to_csv(output_filepath, index=False, encoding="Shift_JIS")


if __name__ == '__main__':
    main()
