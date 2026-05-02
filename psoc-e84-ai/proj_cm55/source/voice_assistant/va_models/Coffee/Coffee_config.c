/****************************************************************************
* File Name        : Coffee_config.c
*
* Description      : This source file contains the configuration object for WWD and NLU
*
* Related Document : See README.md
*
*****************************************************************************
* Copyright 2025, Cypress Semiconductor Corporation (an Infineon company)
* All rights reserved.
*
* This software, including source code, documentation and related
* materials ("Software") is owned by Cypress Semiconductor Corporation
* or one of its affiliates ("Cypress") and is protected by and subject to
* worldwide patent protection (United States and foreign),
* United States copyright laws and international treaty provisions.
* Therefore, you may use this Software only as provided in the license
* agreement accompanying the software package from which you
* obtained this Software ("EULA").
* If no EULA applies, Cypress hereby grants you a personal, non-exclusive,
* non-transferable license to copy, modify, and compile the Software
* source code solely for use in connection with Cypress's
* integrated circuit products.  Any reproduction, modification, translation,
* compilation, or representation of this Software except as specified
* above is prohibited without the express written permission of Cypress.
*
* Disclaimer: THIS SOFTWARE IS PROVIDED AS-IS, WITH NO WARRANTY OF ANY KIND,
* EXPRESS OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, NONINFRINGEMENT, IMPLIED
* WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE. Cypress
* reserves the right to make changes to the Software without notice. Cypress
* does not assume any liability arising out of the application or use of the
* Software or any product or circuit described in the Software. Cypress does
* not authorize its products for use in any products where a malfunction or
* failure of the Cypress product may reasonably be expected to result in
* significant property damage, injury or death ("High Risk Product"). By
* including Cypress's product in a High Risk Product, the manufacturer
* of such system or application assumes all risk of such use and in doing
* so agrees to indemnify Cypress against all liability.
*****************************************************************************/

#include "Coffee_config.h"

#include "mtb_ml.h"
#include "mtb_ml_model_16x8.h"
#include "AM_LSTM_tflm_model_int16x8.h"

#include "ifx_va_prms.h"
#include "ifx_sp_common_priv.h"

#include "Coffee_U55_WWmodel.h"
#include "Coffee_U55_CMDmodel.h"
#include "U55_NMBmodel.h"

#include "Coffee.h"
#include "Coffee_ifx_va_config_prms.h"

/* Following am_tensor_arena has been counted as part of persistent memory total size */
/* Tensor_arena buffer must be in SOCMEM and aligned by 16 which are required by U55 */
static uint8_t am_tensor_arena[AM_LSTM_ARENA_SIZE] __attribute__((aligned(16)))
                                          __attribute__((section(".cy_socmem_data")));


static int16_t data_feed_int[N_SEQ * FEATURE_BUF_SZ] __attribute__((aligned(16)));
static float mtb_ml_input_buffer[N_SEQ * FEATURE_BUF_SZ];

static float xIn[FRAME_SIZE_16K] __attribute__((section(".wwd_nlu_data3")));
static float features[FEATURE_BUF_SZ] __attribute__((section(".wwd_nlu_data4")));
static float output_scores[(N_PHONEMES + 1) * (1 + AM_LOOKBACK)] __attribute__((section(".wwd_nlu_data5")));

//common buffers
static mtb_wwd_nlu_buff_t wwd_nlu_buff =
{
    .am_model_bin = { MTB_ML_MODEL_BIN_DATA(AM_LSTM) },
    .am_model_buffer = {
        .tensor_arena = am_tensor_arena,
        .tensor_arena_size = AM_LSTM_ARENA_SIZE
    },
    .data_feed_int = data_feed_int,
    .mtb_ml_input_buffer = mtb_ml_input_buffer,
    .output_scores = output_scores,
    .xIn = xIn,
    .features = features
};

// NLU setup array
static mtb_nlu_setup_array_t nlu_setup_array =
{
    .intent_name_list = Coffee_intent_name_list,
    .variable_name_list = Coffee_variable_name_list,
    .variable_phrase_list = Coffee_variable_phrase_list,
    .unit_phrase_list = Coffee_unit_phrase_list,
    .intent_map_array = Coffee_intent_map_array,
    .intent_map_array_sizes = Coffee_intent_map_array_sizes,
    .variable_phrase_sizes = Coffee_variable_phrase_sizes,
    .unit_phrase_map_array = Coffee_unit_phrase_map_array,
    .unit_phrase_map_array_sizes = Coffee_unit_phrase_map_array_sizes,
    .NUM_UNIT_PHRASES = sizeof(Coffee_unit_phrase_list),
};

// WW config
static mtb_wwd_conf_t ww_conf = {
    .ww_params = Coffee_dfww_prms,
    .callback.cb_for_event = CY_EVENT_SOD,
    .callback.cb_function = Coffee_wake_word_callback
};

// NLU config
static mtb_nlu_config_t nlu_conf = {
    .nlu_params = Coffee_dfcmd_prms,
    .nlu_command_timeout = 5000,
};

static mtb_wwd_nlu_config_t ww_1_conf = {
    .ww_model_ptr = Coffee_WWmodeldata,
    .cmd_model_ptr = Coffee_CMDmodeldata,
    .nmb_model_ptr = NMBmodeldata,
    .wwd_nlu_buff_data = &wwd_nlu_buff,
    .sod_params = Coffee_sod_prms,
    .hpf_params = Coffee_pre_proc_hpf_prms,
    .denoise_params = Coffee_denoise_prms,
    .ww_conf = &ww_conf,
    .nlu_conf.nlu_config = &nlu_conf,
    .nlu_conf.nlu_variable_data = &nlu_setup_array,
};

mtb_wwd_nlu_config_t *Coffee_ww_nlu_configs[COFFEE_NO_OF_WAKE_WORD] = {&ww_1_conf
};

char *Coffee_ww_str[COFFEE_NO_OF_WAKE_WORD] = {"Coffee Maker"};

const char* Coffee_intent_name_list[COFFEE_NUM_INTENTS] = {
    "ShowDrinks",
    "StartDrink",
    "CustomDrink",
    "ToggleDoubleCup",
    "ToggleColdBrew",
    "ToggleExtraShot",
    "TempSetting",
    "StrengthSetting",
    "VolumeSetting",
    "Stop",
    "Acknowledge",
    "favorite",
};

const char* Coffee_variable_name_list[COFFEE_NUM_VARIABLES] = {
    "Category",
    "Drink",
    "CustomNum",
    "Temp",
    "Strength",
    "Volume",
    "AckVal",
};

const char* Coffee_variable_phrase_list[COFFEE_NUM_VARIABLE_PHRASES] = {
    "Hot", "Cold", "Iced", "Extra Shot", // Category
    "Coffee", "Americano", "Cappuccino", "Espresso", "Cortado", "Latte Machiato", "Cafe Latte", "Cafe Barista", "Hot Water", // Drink
    "", // CustomNum
    "Low", "Medium", "High", // Temp
    "Low", "Medium", "High", // Strength
    "Low", "Medium", "High", // Volume
    "Yes", "No", // AckVal
};

const char* Coffee_unit_phrase_list[COFFEE_NUM_UNIT_PHRASES] = {
    "degree", "degrees", 
    "percent", 
    "level", "levels", 
    "hour", "hours", 
    "minute", "minutes", 
    "second", "seconds", 
    "day", "days", 
    "", 
    "AM", 
    "PM", 
};

const int Coffee_intent_map_array[COFFEE_INTENT_MAP_ARRAY_TOTAL_SIZE] = {
    0, 1, 0, 0, // Show (Hot)
    0, 1, 0, 1, // Show (Cold)
    0, 1, 0, 2, // Show (Iced)
    0, 1, 0, 3, // Show (Extra Shot)
    0, 1, 0, 0, // Which drinks are (Hot)
    0, 1, 0, 1, // Which drinks are (Cold)
    0, 1, 0, 2, // Which drinks are (Iced)
    0, 1, 0, 3, // Which drinks are (Extra Shot)
    0, 1, 0, 0, // Which drinks can I brew (Hot)
    0, 1, 0, 1, // Which drinks can I brew (Cold)
    0, 1, 0, 2, // Which drinks can I brew (Iced)
    0, 1, 0, 3, // Which drinks can I brew (Extra Shot)
    0, 1, 0, 0, // Show drinks that can be (Hot)
    0, 1, 0, 1, // Show drinks that can be (Cold)
    0, 1, 0, 2, // Show drinks that can be (Iced)
    0, 1, 0, 3, // Show drinks that can be (Extra Shot)
    0, 1, 0, 0, // Which drinks allow for (Hot)
    0, 1, 0, 1, // Which drinks allow for (Cold)
    0, 1, 0, 2, // Which drinks allow for (Iced)
    0, 1, 0, 3, // Which drinks allow for (Extra Shot)
    1, 1, 1, 0, // Brew (Coffee)
    1, 1, 1, 1, // Brew (Americano)
    1, 1, 1, 2, // Brew (Cappuccino)
    1, 1, 1, 3, // Brew (Espresso)
    1, 1, 1, 4, // Brew (Cortado)
    1, 1, 1, 5, // Brew (Latte Machiato)
    1, 1, 1, 6, // Brew (Cafe Latte)
    1, 1, 1, 7, // Brew (Cafe Barista)
    1, 1, 1, 8, // Brew (Hot Water)
    1, 1, 1, 0, // Start (Coffee)
    1, 1, 1, 1, // Start (Americano)
    1, 1, 1, 2, // Start (Cappuccino)
    1, 1, 1, 3, // Start (Espresso)
    1, 1, 1, 4, // Start (Cortado)
    1, 1, 1, 5, // Start (Latte Machiato)
    1, 1, 1, 6, // Start (Cafe Latte)
    1, 1, 1, 7, // Start (Cafe Barista)
    1, 1, 1, 8, // Start (Hot Water)
    1, 1, 1, 0, // Make (Coffee)
    1, 1, 1, 1, // Make (Americano)
    1, 1, 1, 2, // Make (Cappuccino)
    1, 1, 1, 3, // Make (Espresso)
    1, 1, 1, 4, // Make (Cortado)
    1, 1, 1, 5, // Make (Latte Machiato)
    1, 1, 1, 6, // Make (Cafe Latte)
    1, 1, 1, 7, // Make (Cafe Barista)
    1, 1, 1, 8, // Make (Hot Water)
    1, 1, 1, 0, // Make me a (Coffee)
    1, 1, 1, 1, // Make me a (Americano)
    1, 1, 1, 2, // Make me a (Cappuccino)
    1, 1, 1, 3, // Make me a (Espresso)
    1, 1, 1, 4, // Make me a (Cortado)
    1, 1, 1, 5, // Make me a (Latte Machiato)
    1, 1, 1, 6, // Make me a (Cafe Latte)
    1, 1, 1, 7, // Make me a (Cafe Barista)
    1, 1, 1, 8, // Make me a (Hot Water)
    2, 1, 2, -1, // Custom brew <numbers10> {}
    2, 1, 2, -1, // Personal drink <numbers10> {}
    2, 1, 2, -1, // My drink <numbers10> {}
    3, 0, // Double cup mode
    3, 0, // Double cup <end-silence>
    4, 0, // Cold brew mode
    4, 0, // Cold brew <end-silence>
    5, 0, // Extra shot mode
    5, 0, // Extra shot <end-silence>
    6, 1, 3, 0, // Temp (Low)
    6, 1, 3, 1, // Temp (Medium)
    6, 1, 3, 2, // Temp (High)
    6, 1, 3, 0, // Temp setting (Low)
    6, 1, 3, 1, // Temp setting (Medium)
    6, 1, 3, 2, // Temp setting (High)
    6, 1, 3, 0, // Temp level (Low)
    6, 1, 3, 1, // Temp level (Medium)
    6, 1, 3, 2, // Temp level (High)
    7, 1, 4, 0, // Strength (Low)
    7, 1, 4, 1, // Strength (Medium)
    7, 1, 4, 2, // Strength (High)
    7, 1, 4, 0, // Strength setting (Low)
    7, 1, 4, 1, // Strength setting (Medium)
    7, 1, 4, 2, // Strength setting (High)
    7, 1, 4, 0, // Strength level (Low)
    7, 1, 4, 1, // Strength level (Medium)
    7, 1, 4, 2, // Strength level (High)
    8, 1, 5, 0, // Volume (Low)
    8, 1, 5, 1, // Volume (Medium)
    8, 1, 5, 2, // Volume (High)
    8, 1, 5, 0, // Volume setting (Low)
    8, 1, 5, 1, // Volume setting (Medium)
    8, 1, 5, 2, // Volume setting (High)
    8, 1, 5, 0, // Volume level (Low)
    8, 1, 5, 1, // Volume level (Medium)
    8, 1, 5, 2, // Volume level (High)
    9, 0, // Stop
    9, 0, // Cancel
    9, 0, // End
    10, 1, 6, 0, // (Yes)
    10, 1, 6, 1, // (No)
    11, 0, // Make my favorite drink
    11, 0, // Start special drink
};

const int Coffee_intent_map_array_sizes[COFFEE_NUM_COMMANDS] = {
    4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 
    4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 
    4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 2, 2, 2, 2, 2, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 
    4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 4, 2, 2, 2, 4, 4, 2, 2, 
};

const int Coffee_variable_phrase_sizes[COFFEE_NUM_VARIABLES] = {
    4, // Category: (Hot,Cold,Iced,Extra Shot)
    9, // Drink: (Coffee,Americano,Cappuccino,Espresso,Cortado,Latte Machiato,Cafe Latte,Cafe Barista,Hot Water)
    0, // CustomNum: None
    3, // Temp: (Low,Medium,High)
    3, // Strength: (Low,Medium,High)
    3, // Volume: (Low,Medium,High)
    2, // AckVal: (Yes,No)
};

const int Coffee_unit_phrase_map_array[COFFEE_UNIT_PHRASE_MAP_ARRAY_TOTAL_SIZE] = {
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    1, 1, 13, // {}
    1, 1, 13, // {}
    1, 1, 13, // {}
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
    0, // None
};

const int Coffee_unit_phrase_map_array_sizes[COFFEE_NUM_COMMANDS] = {
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 
    1, 1, 1, 1, 1, 1, 3, 3, 3, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 
    1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 1, 
};



__attribute__((weak)) void Coffee_wake_word_callback(mtb_wwd_nlu_events_t event)
{

}
