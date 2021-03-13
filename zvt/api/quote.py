# -*- coding: utf-8 -*-
from typing import Union, List

import numpy as np
import pandas as pd

from zvt.api.data_type import Region, Provider, EntityType
from zvt.domain import ReportPeriod, Fund, Etf
from zvt.contract import IntervalLevel, AdjustType, Mixin, PortfolioStockHistory
from zvt.contract.api import decode_entity_id, get_schema_by_name
from zvt.utils.pd_utils import pd_is_not_null
from zvt.utils.time_utils import to_pd_timestamp


def get_kdata_schema(entity_type: EntityType,
                     level: Union[IntervalLevel, str] = IntervalLevel.LEVEL_1DAY,
                     adjust_type: Union[AdjustType, str] = None):
    if type(level) == str:
        level = IntervalLevel(level)
    if type(adjust_type) == str:
        adjust_type = AdjustType(adjust_type)

    # kdata schema rule
    # 1)name:{SecurityType.value.capitalize()}{IntervalLevel.value.upper()}Kdata
    if adjust_type and (adjust_type != AdjustType.qfq):
        schema_str = '{}{}{}Kdata'.format(entity_type.value.capitalize(), level.value.capitalize(),
                                          adjust_type.value.capitalize())
    else:
        schema_str = '{}{}Kdata'.format(entity_type.value.capitalize(), level.value.capitalize())
    return get_schema_by_name(schema_str)


def get_recent_report_date(the_date, step=0):
    the_date = to_pd_timestamp(the_date)
    assert step >= 0
    if the_date.month >= 10:
        recent = "{}{}".format(the_date.year, '-09-30')
    elif the_date.month >= 7:
        recent = "{}{}".format(the_date.year, '-06-30')
    elif the_date.month >= 4:
        recent = "{}{}".format(the_date.year, '-03-31')
    else:
        recent = "{}{}".format(the_date.year - 1, '-12-31')

    if step == 0:
        return recent
    else:
        step = step - 1
        return get_recent_report_date(recent, step)


# def get_recent_report_period(the_date, step=0):
#     return to_report_period_type(get_recent_report_date(the_date, step=step))


def get_exchange(code):
    if code >= '333333':
        return 'sh'
    else:
        return 'sz'


def china_stock_code_to_id(code):
    return "{}_{}_{}".format('stock', get_exchange(code), code)





def to_high_level_kdata(kdata_df: pd.DataFrame, to_level: IntervalLevel):
    def to_close(s):
        if pd_is_not_null(s):
            return s[-1]

    def to_open(s):
        if pd_is_not_null(s):
            return s[0]

    def to_high(s):
        return np.max(s)

    def to_low(s):
        return np.min(s)

    def to_sum(s):
        return np.sum(s)

    original_level = kdata_df['level'][0]
    entity_id = kdata_df['entity_id'][0]
    provider = kdata_df['provider'][0]
    name = kdata_df['name'][0]
    code = kdata_df['code'][0]

    entity_type, _, _ = decode_entity_id(entity_id=entity_id)

    assert IntervalLevel(original_level) <= IntervalLevel.LEVEL_1DAY
    assert IntervalLevel(original_level) < IntervalLevel(to_level)

    df: pd.DataFrame = None
    if to_level == IntervalLevel.LEVEL_1WEEK:
        # loffset='-2'　用周五作为时间标签
        if entity_type == EntityType.Stock:
            df = kdata_df.resample('W', loffset=pd.DateOffset(days=-2)).apply({'close': to_close,
                                                                               'open': to_open,
                                                                               'high': to_high,
                                                                               'low': to_low,
                                                                               'volume': to_sum,
                                                                               'turnover': to_sum})
        else:
            df = kdata_df.resample('W', loffset=pd.DateOffset(days=-2)).apply({'close': to_close,
                                                                               'open': to_open,
                                                                               'high': to_high,
                                                                               'low': to_low,
                                                                               'volume': to_sum,
                                                                               'turnover': to_sum})
    df = df.dropna()
    # id        entity_id  timestamp   provider    code  name level
    df['entity_id'] = entity_id
    df['provider'] = provider
    df['code'] = code
    df['name'] = name
    return df


def portfolio_relate_stock(df, portfolio):
    df['entity_id'] = portfolio.entity_id
    df['entity_type'] = portfolio.entity_type
    df['exchange'] = portfolio.exchange
    df['code'] = portfolio.code
    df['name'] = portfolio.name
    return df


# 季报只有前十大持仓，半年报和年报才有全量的持仓信息，故根据离timestamp最近的报表(年报 or 半年报)来确定持仓
def get_portfolio_stocks(region: Region, provider: Provider, timestamp, portfolio_entity=Fund, code=None, codes=None, ids=None):
    portfolio_stock = f'{portfolio_entity.__name__}Stock'
    data_schema: PortfolioStockHistory = get_schema_by_name(portfolio_stock)
    latests: List[PortfolioStockHistory] = data_schema.query_data(region=region,
                                                                  provider=provider,
                                                                  code=code,
                                                                  end_timestamp=timestamp,
                                                                  order=data_schema.timestamp.desc(),
                                                                  limit=1,
                                                                  return_type='domain')
    if latests:
        latest_record = latests[0]
        # 获取最新的报表
        df = data_schema.query_data(region=region, provider=provider, code=code, codes=codes, ids=ids, end_timestamp=timestamp,
                                    filters=[data_schema.report_date == latest_record.report_date])
        # 最新的为年报或者半年报
        if latest_record.report_period == ReportPeriod.year or latest_record.report_period == ReportPeriod.half_year:
            return df
        # 季报，需要结合 年报或半年报 来算持仓
        else:
            step = 0
            while step <= 20:
                report_date = get_recent_report_date(latest_record.report_date, step=step)

                pre_df = data_schema.query_data(region=region, provider=provider, code=code,
                                                codes=codes, ids=ids, end_timestamp=timestamp,
                                                filters=[data_schema.report_date == to_pd_timestamp(report_date)])
                df = df.append(pre_df)

                # 半年报和年报
                if (ReportPeriod.half_year.value in pre_df['report_period'].tolist()) or (
                        ReportPeriod.year.value in pre_df['report_period'].tolist()):
                    # 保留最新的持仓
                    df = df.drop_duplicates(subset=['stock_code'], keep='first')
                    return df
                step = step + 1


# etf半年报和年报才有全量的持仓信息，故根据离timestamp最近的报表(年报 or 半年报)来确定持仓
def get_etf_stocks(region: Region, provider: Provider, timestamp, code=None, codes=None, ids=None):
    return get_portfolio_stocks(region=region, provider=provider, timestamp=timestamp,
                                portfolio_entity=Etf, code=code, codes=codes, ids=ids)


def get_fund_stocks(region: Region, provider: Provider, timestamp, code=None, codes=None, ids=None):
    return get_portfolio_stocks(region=region, provider=provider, timestamp=timestamp,
                                portfolio_entity=Fund, code=code, codes=codes, ids=ids)


def get_kdata(region: Region, entity_id=None, entity_ids=None,
              level=IntervalLevel.LEVEL_1DAY.value, provider=None, columns=None,
              return_type='df', start_timestamp=None, end_timestamp=None,
              filters=None, session=None, order=None, limit=None,
              index='timestamp', adjust_type: AdjustType = None):
    assert not entity_id or not entity_ids
    if entity_ids:
        entity_id = entity_ids[0]
    else:
        entity_ids = [entity_id]

    entity_type, exchange, code = decode_entity_id(entity_id)
    data_schema: Mixin = get_kdata_schema(entity_type, level=level, adjust_type=adjust_type)

    return data_schema.query_data(region=region, entity_ids=entity_ids, level=level,
                                  provider=provider, columns=columns,
                                  return_type=return_type,
                                  start_timestamp=start_timestamp,
                                  end_timestamp=end_timestamp,
                                  filters=filters, session=session, order=order,
                                  limit=limit, index=index)


if __name__ == '__main__':
    from zvt.api.data_type import Region
    df = get_etf_stocks(Region.CHN, timestamp=now_pd_timestamp(Region.CHN), code='510050', provider=Provider.JoinQuant)
    print(df)


# the __all__ is generated
__all__ = ['get_kdata_schema', 'get_recent_report_date', 'get_exchange', 'china_stock_code_to_id', 'to_high_level_kdata',
           'portfolio_relate_stock', 'get_etf_stocks', 'get_kdata', 'get_portfolio_stocks']
