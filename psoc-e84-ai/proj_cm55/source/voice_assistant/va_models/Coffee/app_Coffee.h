#ifndef APP_COFFEE_h
#define APP_COFFEE_h

#include "Coffee.h"

#define  COFFEE_INTENT_ShowDrinks               0x0
#define  COFFEE_INTENT_StartDrink               0x1
#define  COFFEE_INTENT_CustomDrink              0x2
#define  COFFEE_INTENT_ToggleDoubleCup          0x3
#define  COFFEE_INTENT_ToggleColdBrew           0x4
#define  COFFEE_INTENT_ToggleExtraShot          0x5
#define  COFFEE_INTENT_TempSetting              0x6
#define  COFFEE_INTENT_StrengthSetting          0x7
#define  COFFEE_INTENT_VolumeSetting            0x8
#define  COFFEE_INTENT_Stop                     0x9
#define  COFFEE_INTENT_Acknowledge              0xa
#define  COFFEE_INTENT_favorite                 0xb

#define  COFFEE_VARIABLE_Category_Hot                 0x0
#define  COFFEE_VARIABLE_Category_Cold                0x1
#define  COFFEE_VARIABLE_Category_Iced                0x2
#define  COFFEE_VARIABLE_Category_ExtraShot           0x3
#define  COFFEE_VARIABLE_Drink_Coffee                 0x4
#define  COFFEE_VARIABLE_Drink_Americano              0x5
#define  COFFEE_VARIABLE_Drink_Cappuccino             0x6
#define  COFFEE_VARIABLE_Drink_Espresso               0x7
#define  COFFEE_VARIABLE_Drink_Cortado                0x8
#define  COFFEE_VARIABLE_Drink_LatteMachiato          0x9
#define  COFFEE_VARIABLE_Drink_CafeLatte              0xa
#define  COFFEE_VARIABLE_Drink_CafeBarista            0xb
#define  COFFEE_VARIABLE_Drink_HotWater               0xc
#define  COFFEE_VARIABLE_CustomNum_                   0xd
#define  COFFEE_VARIABLE_Temp_Low                     0xe
#define  COFFEE_VARIABLE_Temp_Medium                  0xf
#define  COFFEE_VARIABLE_Temp_High                    0x10
#define  COFFEE_VARIABLE_Strength_Low                 0x11
#define  COFFEE_VARIABLE_Strength_Medium              0x12
#define  COFFEE_VARIABLE_Strength_High                0x13
#define  COFFEE_VARIABLE_Volume_Low                   0x14
#define  COFFEE_VARIABLE_Volume_Medium                0x15
#define  COFFEE_VARIABLE_Volume_High                  0x16
#define  COFFEE_VARIABLE_AckVal_Yes                   0x17
#define  COFFEE_VARIABLE_AckVal_No                    0x18

#define  COFFEE_UNIT_degree                   0x0
#define  COFFEE_UNIT_degrees                  0x1
#define  COFFEE_UNIT_percent                  0x2
#define  COFFEE_UNIT_level                    0x3
#define  COFFEE_UNIT_levels                   0x4
#define  COFFEE_UNIT_hour                     0x5
#define  COFFEE_UNIT_hours                    0x6
#define  COFFEE_UNIT_minute                   0x7
#define  COFFEE_UNIT_minutes                  0x8
#define  COFFEE_UNIT_second                   0x9
#define  COFFEE_UNIT_seconds                  0xa
#define  COFFEE_UNIT_day                      0xb
#define  COFFEE_UNIT_days                     0xc
#define  COFFEE_UNIT_                         0xd
#define  COFFEE_UNIT_AM                       0xe
#define  COFFEE_UNIT_PM                       0xf

#endif // APP_COFFEE_h
