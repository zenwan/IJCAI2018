# coding: UTF-8
import time
import pandas as pd
import lightgbm as lgb
import numpy as np
import scipy as sp
from sklearn.preprocessing import LabelEncoder
import warnings
warnings.filterwarnings("ignore")


# 评价函数
def logloss(act, pred):
    epsilon = 1e-15
    pred = sp.maximum(epsilon, pred)
    pred = sp.minimum(1-epsilon, pred)
    ll = sum(act*sp.log(pred) + sp.subtract(1, act)*sp.log(sp.subtract(1, pred)))
    ll = ll * -1.0/len(act)
    return ll


# 时间转换函数
def timestamp_datetime(value):
    format = '%Y-%m-%d %H:%M:%S'
    value = time.localtime(value)
    dt = time.strftime(format, value)
    return dt


# 转换标签编码
def LabEncode(data, col):
    le = LabelEncoder()
    ipl = le.fit_transform(data[col])
    data[col] = ipl
    return data


# 对数据进行归一化
def min_max_normalize(df, name):
    # 归一化
    max_number = df[name].max()
    min_number = df[name].min()
    df[name] = df[name].map(lambda x: float(x - min_number + 1) / float(max_number - min_number + 1))
    return df


# 基础的数据处理和特征构造
def base_process(data):
    le = LabelEncoder()
    for col in ['shop_id', 'user_id', 'item_id', 'item_brand_id', 'item_city_id']:
        data = LabEncode(data, col)
    data['len_property_list'] = data['item_property_list'].apply(lambda x: len(str(x).split(';')))
    for i in range(10):
        data['item_property_list' + str(i)] = le.fit_transform(data['item_property_list'].apply(lambda x: str(str(x).split(';')[i]) if len(str(x).split(';')) > i else 0))
    data = LabEncode(data, 'context_page_id')
    data = data.context_page_id.apply(lambda x: 1 if x < 5 else 2)
    return data


# 不同时间粒度（天，小时）
def convert_data(data):
    data['time'] = data.context_timestamp.apply(timestamp_datetime)
    data['day'] = data.time.apply(lambda x: int(x[8:10]))
    data['hour'] = data.time.apply(lambda x: int(x[11:13]))
    data['hour_sin'] = data['hour'].map(lambda x: np.pi*np.sin(int(x)))
    data['hour_cos'] = data['hour'].map(lambda x: np.pi*np.cos(int(x)))
    return data


# 提取用户点击行为的统计特征
def user_item_feat(data):
    user_query_day = data.groupby(['user_id', 'day']).size(
    ).reset_index().rename(columns={0: 'user_query_day'})
    data = pd.merge(data, user_query_day, 'left', on=['user_id', 'day'])
    user_query_day_hour = data.groupby(['user_id', 'day', 'hour']).size().reset_index().rename(
        columns={0: 'user_query_day_hour'})
    data = pd.merge(data, user_query_day_hour, 'left',
                    on=['user_id', 'day', 'hour'])
    user_item_day = data.groupby(['user_id', 'item_id', 'day']).size(
    ).reset_index().rename(columns={0: 'user_item_day'})
    data = pd.merge(data, user_item_day, 'left', on=['user_id', 'item_id', 'day'])
    user_item_day_hour = data.groupby(['user_id', 'item_id', 'day', 'hour']).size().reset_index().rename(
        columns={0: 'user_item_day_hour'})
    data = pd.merge(data, user_item_day_hour, 'left',
                    on=['user_id', 'item_id', 'day', 'hour'])
    return data


# 分类特征
def category_feat(data):
    data['item_category_list'] = data.item_category_list.apply(lambda s: (int([d for d in s.split(';')][1])))
    return data


# 商品按照类别、品牌、属性和城市做一个价格、销量、收藏、展示的均值
def item_mean_ratio(df, colname):
    grouped = df.groupby([colname])
    meancols = ['item_price_level', 'item_sales_level', 'item_collected_level', 'item_pv_level']
    df_g = grouped[meancols].mean().reset_index()
    colnames = [i for i in df_g.columns]
    for i in range(len(colnames)):
        if colnames[i] != colname:
            colnames[i] += '_mean_by_'+colname.split('_')[1]
    df_g.columns = colnames
    df=pd.merge(df, df_g, how='left', on=colname)
    colnames = colnames[1:]
    for i in range(len(colnames)):
        df[colnames[i]+'_ratio'] = df[meancols[i]]/df[colnames[i]]
    return df


# 线下训练和验证
def lgbCV(train, test):
    del_feat = ['instance_id', 'item_property_list', 'user_id',
                'context_id', 'context_timestamp', 'predict_category_property', 'time', 'is_trade']
    all_features = data.columns.values.tolist()
    features = []
    for fe in all_features:
        if fe not in del_feat:
            features.append(fe)

    X = train[features]
    y = train['is_trade'].values
    X_tes = test[features]
    y_tes = test['is_trade'].values
    gbm = lgb.LGBMClassifier(objective='binary',
                             # metric='binary_error',
                             num_leaves=63,
                             depth=7,
                             learning_rate=0.05,
                             seed=2018,
                             colsample_bytree=0.8,
                             # min_child_samples=8,
                             subsample=0.9,
                             n_estimators=20000)

    gbm.fit(X, y, eval_set=[(X, y), (X_tes, y_tes)], early_stopping_rounds=200)
    pre = gbm.predict_proba(X_tes)[:, 1]
    best_iter = gbm.best_iteration_
    print '本地cv', logloss(y_tes, pre)

    # 查看属性重要性
    df = pd.DataFrame(columns=['feature', 'important'])
    df['feature'] = features
    df['important'] = gbm.feature_importances_
    df = df.sort_values(axis=0, ascending=True, by='important').reset_index()
    print df
    return best_iter


# 线上提交
def submit(train, test, best_iter):
    del_feat = ['instance_id', 'item_property_list', 'user_id',
                'context_id', 'context_timestamp', 'predict_category_property', 'time', 'is_trade']
    all_features = data.columns.values.tolist()
    features = []
    for fe in all_features:
        if fe not in del_feat:
            features.append(fe)

    X = train[features]
    y = train['is_trade'].values
    X_tes = test[features]
    gbm = lgb.LGBMClassifier(objective='binary',
                             # metric='binary_error',
                             num_leaves=35,
                             depth=8,
                             learning_rate=0.05,
                             seed=2018,
                             colsample_bytree=0.8,
                             # min_child_samples=8,
                             subsample=0.9,
                             n_estimators=best_iter)

    gbm.fit(X, y)
    pre = gbm.predict_proba(X_tes)[:, 1]
    pd_result = pd.DataFrame({'instance_id': test["instance_id"], 'predicted_score': pre})
    pd_result.to_csv('result/result_xgb_v3.txt', index=False, sep=" ", float_format='%.6f')
    print '...完成'


dir = 'data/oria/'
if __name__ == "__main__":
    train = pd.read_csv(dir + 'round1_ijcai_18_train_20180301.txt', sep=' ')
    train.drop_duplicates(inplace=True)
    test = pd.read_csv(dir + 'round1_ijcai_18_test_a_20180301.txt', sep=' ')
    data = pd.concat([train, test])
    # 基础数据处理
    # data = base_process(data)
    # 转换日期并做一些基本特征
    data = convert_data(data)
    # 提取用户点击行为的统计特征
    data = user_item_feat(data)
    # 分类特征
    data = category_feat(data)
    # 商品按照类别、品牌、属性和城市做一个价格、销量、收藏、展示的均值
    data = item_mean_ratio(data, 'item_category_list')
    data = item_mean_ratio(data, 'item_brand_id')
    data = item_mean_ratio(data, 'item_city_id')

    # 划分训练集和验证集------------------------线下训练验证
    train = data.loc[data.day < 24]  # 18,19,20,21,22,23,24
    test = data.loc[data.day == 24]  # 暂时先使用第24天作为验证集
    best_iter = lgbCV(train, test)

    # 线上训练和提交---------------------------线上训练和提交
    train = data[data.is_trade.notnull()]
    test = data[data.is_trade.isnull()]
    submit(train, test, best_iter)

#   作为基础版本
#   线下：0.0810834542236 线上：0.08240
