# **How the Automatic Pro Profile works** {#how-the-automatic-pro-profile-works}

## (and how to adapt it to other quantities)

[**How the Automatic Pro Profile works	1**](#how-the-automatic-pro-profile-works)

[1\. Cheatsheet (Quick Reference)	2](#1.-cheatsheet-\(quick-reference\))

[Quick Settings (adjustable via display)	2](#quick-settings-\(adjustable-via-display\))

[Recommendations	2](#recommendations)

[How to adapt	2](#how-to-adapt)

[Default Example 18g	2](#default-example-18g)

[Settings Summary	2](#settings-summary)

[The Profile Steps (at a glance)	3](#the-profile-steps-\(at-a-glance\))

[2\. Detailed Explanation	4](#2.-detailed-explanation)

[Phase 1: Pre-Infusion	4](#phase-1:-pre-infusion)

[Phase 2: Bloom	4](#phase-2:-bloom)

[Phase 3: Ramp-Up	4](#phase-3:-ramp-up)

[Phase 4: Brewing	5](#phase-4:-brewing)

[**Customized AI analysis for the Automatic Pro profile (Experimental)	6**](#customized-ai-analysis-for-the-automatic-pro-profile-\(experimental\))

[How to use	6](#how-to-use)

[Prompt	8](#prompt)

## 

## **1\. Cheatsheet (Quick Reference)** {#1.-cheatsheet-(quick-reference)}

### **Quick Settings *(adjustable via display)*** {#quick-settings-(adjustable-via-display)}

* **For weight mode: Weight \= Dose × Ratio**  
  * *Example: 18g in, 1:2 ratio: 2 × 18g \= 36g*  
* **For time mode: Time \= (Dose × Ratio) / Flow \+ 16s**  
  * *Example: 18g in, 1:2 ratio: (2 × 18g) / 1.8g/s \+ 16s \= 36s*

### **Recommendations** {#recommendations}

* **Med-Dark Roasts**  
  * \~91°C  
  * Ratio: 1:1.5 – 1:2.5 *and lower (Time ≈ 36s)*

* **Med-Light Roasts**  
  * \~94°C  
  * Ratio: 1:2.5 – 1:3 *and higher (Time ≈ 46s)*

* **Lungo**  
  * Increase target weight or use Time Mode

* **Other Tips**  
  * Sour/salty? \-\> Go for a ratio of 1:2.5+ *(also try higher temperatures)*  
  * Bitter/dry? \-\> Go for a ratio of 1:1.5- *(also try lower temperatures)*

### **How to adapt** {#how-to-adapt}

* **Flow (Phase 2,3 & 4\)**  
  * *Calculate based on an ideal shot time not recipe (brewing phase only, traditionally, one would say from the first drop in the cup).*  
  * **Yield / Time**   
    * *Example for a 1:2 ratio: 36ml / 20s \= 1.8g/s*  
* **Phase 1: Pre-Infusion; water drawn**  
  * **Dose × 1.3 \+ Headspace** *(of your portafilter)*  
* **Phase 3: Weight reached**  
  * **Flow × Phase 3 Duration**  
* *For more, check detailed explanation*

### **Default Example 18g** {#default-example-18g}

91°C, 18g Dose → 36g Yield (1:2 Ratio), for Classic Espresso (Medium-Dark Roasts) 

#### **Settings Summary** {#settings-summary}

* **Yield / Ratio:**  
  * *Default:* 1:2 (Stop at \~36g)  
* **Time Mode (No Scale):**  
  * Set the last phase (Brewing) to \~14 seconds (or \~36s Total Time).

#### **The Profile Steps (at a glance)** {#the-profile-steps-(at-a-glance)}

| Phase | Goal | Flow | Pressure Limit | Time | Stop Trigger |
| :---- | :---- | :---- | :---- | :---- | :---- |
| **1\. Pre-Infusion** | Fill headspace, moisten puck. | 20 g/s | 2 bar | 10 s | 1g (Weight)31ml (Water drawn) |
| **2\. Bloom** | Saturate puck, prevent channeling. | 1.8 g/s | 2 bar | 1–10 s | 1.5g (Weight) |
| **3\. Ramp-Up** | Build pressure smoothly. | 1.8 g/s | 12 bar | 6 s | 11g (Weight)\* |
| **4\. Brewing** | Main extraction. | 1.8 g/s | 9 bar | 120 s | 36g (Weight)\*\* |

*\*Calculated safety limit (1.8g/s × 6s).* *\*\*Or manual stop.*

---

## 

## **2\. Detailed Explanation** {#2.-detailed-explanation}

### **Phase 1: Pre-Infusion** {#phase-1:-pre-infusion}

**Goal:** Quickly fill the headspace and moisten the puck without building pressure. First, we must pump enough water to completely saturate the coffee. We calculate this based on the dose and headspace.

* **Calculation: Dose × 1.3 \+ Headspace.**  
* *Example (18g basket):* 18g \* 1.3 \+ 7.5ml ≈ 31ml (Water drawn).

To fill this volume quickly, we set a high flow rate (20 g/s). We allow 10 seconds for this phase, ensuring it completes even if the machine pumps slower (e.g., 4 g/s). **Crucial:** This must happen without pressure to avoid premature extraction. We limit the pressure to **2 bar**. **Safety Stop:** The phase ends immediately if 1g of coffee lands in the cup.  
*Note: The water drawn stop parameter prevents a long pre-infusion if it does not drip and enables a significantly more reliable pre-infusion in time mode. If you use a scale, you can also delete the water drawn parameter entirely if you do not find it helpful.*

### **Phase 2: Bloom** {#phase-2:-bloom}

**Goal:** Saturate the puck, prevent channeling, and wait for extraction readiness. Phase 2 ensures the puck is fully soaked. Instead of a static wait, we maintain a slight flow (1.8 g/s, matching the main brew flow) to make the bloom effective. We keep the pressure limit at **2 bar**. This effectively pauses the water flow if the puck is already pressurized/saturated.  
**Safety Stop:** The phase ends immediately if 1.5g of coffee lands in the cup.

* **Duration:** 1–10 seconds.  
* *Tip:* A longer bloom time and/or a higher stop weight allows for finer grinding.

### **Phase 3: Ramp-Up** {#phase-3:-ramp-up}

**Goal:** Build pressure smoothly. We maintain the flow rate of 1.8 g/s but raise the pressure limit to **12 bar**. This facilitates a smooth pressure ramp-up following pre-infusion. While 9 bar is generally the target, the higher 12-bar ceiling accommodates finer grinds that require an extra "push" to start flowing. We cap this phase at **6 seconds** to prevent pressure spikes on coarser grinds  
**Safety Stop:** The phase ends immediately if **11g** (6s × 1.8g/s) of coffee lands in the cup (**Flow × Phase 3 Duration**).

**Note on Profile Logic:** Previously, we used increased flow to build pressure quickly, but this often caused spikes. We now use continuous flow for consistency.

* *Comparison:* The **Adaptive v2** profile uses a pressure-based ramp-up to transfer flow rates (good for varying doses), while **Pure Flow** skips this phase entirely. Each method has its merits, so feel free to experiment. *Future updates may use real-time puck flow data for an even better ramp-up.*

### **Phase 4: Brewing** {#phase-4:-brewing}

**Goal:** Main extraction at constant flow. The flow rate is crucial here. We calculate it based on an ideal shot time. (Time measurement starts for our calculations from the first drop).

* We can **use 20 seconds** for every dose at a 1:2 extraction ratio:  
* **Calculation: Dose × 2 / 20s \= Flow**  
* *Example: (18g basket):* 18g × 2 / 20s ≈ 1.8g/s  
* If we were to use this calculation for a hypothetical 1:2.5 extraction ratio, both the target weight and the time would be extended, usually proportionally. For us, however, this means that we can only calculate the flow based on the 1:2 extraction time.

**Pressure & Flavor:** To prevent bitterness, we limit pressure to **9 bar**. If the coffee is ground very finely, you might see a **"Second Blooming" effect**: The transition from the 12-bar ramp-up (Phase 3\) to the 9-bar limit (Phase 4\) can cause the flow to briefly pause as the system regulates. This is not an error. It often produces a rich, chocolatey profile rather than a bitter one.

* *Recommendation:* Try this with chocolatey/nutty roasts at a 1:1.5 ratio.

**Limits & Safety:** We set a generous **120-second duration**. This allows for high-resistance shots or long coffees if you grind coarser.

* **For weight mode: Weight \= Dose × Ratio**  
  * *Example: 18g in, 1:2 ratio: 2 × 18g \= 36g*  
* **For time mode: Time \= (Dose × Ratio) / Flow \+ 16s**  
  * *Example: 18g in, 1:2 ratio: (2 × 18g) / 1.8g/s \+ 16s \= 36s*  
    * *16s \= Duration phase 1 \+ Duration phase 2*  
  * ***Or change time directly in your profile: Last phase duration \= (Dose × Ratio) / Flow \- Duration phase 3***

# Customized AI analysis for the Automatic Pro profile *(Experimental)* {#customized-ai-analysis-for-the-automatic-pro-profile-(experimental)}

**Link for Gemini: [https://gemini.google.com/share/8a4992c8635a](https://gemini.google.com/share/8a4992c8635a)**  
*Note:* 

- *If you respond in your preferred language, the AI should switch to your language automatically.*  
- *Analyzing this with AI is experimental. In general, the more information you provide, the more accurate the result will be. Some recommendations can also be counterproductive.*

## How to use {#how-to-use}

* **Preparation:**  
  If you do not reach the final chat after clicking on the link above, you can simply copy the prompt and start your own chat (alternatively, you can find the prompt at the end of the document).  
  ![][image1]

1. **When you execute the prompt, or when the chat has started, something like this should appear in the chat:**  
   ![][image2]

2. **Now enter the desired information. If you cannot provide all the information, simply leave it out. If you would like to provide additional information or make any other comments, you can do so. Normally, everything works very well regardless.**   
   ***Example:*****![][image3]**

3. **Wait for the response.** Normally, the AI will explain what it has recognized so that you can check whether the AI is providing incorrect information.  
   After the explanation, it should suggest what you should adjust.  
   In the example, it recommends grinding more coarsely and lowering the temperature. These are useful suggestions that I would have made myself. *(Note here that the temperature was read from the graph, and mine was too high due to the Kff function. You could add your set temperature to the information, to make such things clearer)*:![][image4]

## Prompt {#prompt}

*For copying, tested with Gemini:*

| `I need you now as an experienced specialty barista. I have created a general flow profile for a GaggiMate (Gaggia Classic with Decent Espresso machine functions), which you should strictly adhere to. You should analyze espresso shots for me. So don't change anything in the Automatic Pro profile for now, but assess what needs to be adjusted. Since you should help not only me but also others who are not so familiar with the profile, please send them all the information you need for an analysis, e.g.: 1. How many grams in 2. What kind of beans and, if applicable, what they should taste like according to the roaster 3. What they are aiming for 4. What didn't taste good 5. And a screenshot of the shot graph so you can analyze the shot. (Very important!) Here is the profile you should strictly adhere to. This means that the user should later change the grind size, temperature, ratio, weight, and time in particular: How the Automatic Pro Profile works (and how to adapt it to other quantities) How the Automatic Pro Profile works	1 1. Cheatsheet (Quick Reference)	2 Quick Settings (adjustable via display)	2 Recommendations	2 How to adapt	2 Default Example 18g	2 Settings Summary	2 The Profile Steps (at a glance)	3 2. Detailed Explanation	4 Phase 1: Pre-Infusion	4 Phase 2: Bloom	4 Phase 3: Ramp-Up	4 Phase 4: Brewing	4 1. Cheatsheet (Quick Reference) Quick Settings (adjustable via display) For weight mode: Weight = Dose × Ratio Example: 18g in, 1:2 ratio: 2 × 18g = 36g For time mode: Time = (Dose × Ratio) / Flow + 16s Example: 18g in, 1:2 ratio: (2 × 18g) / 1.8g/s + 16s = 36s Recommendations Med-Dark Roasts ~91°C Ratio: 1:1.5 – 1:2.5 and lower (Time ≈ 36s) Med-Light Roasts ~94°C Ratio: 1:2.5 – 1:3 and higher (Time ≈ 46s) Lungo Increase target weight or use Time Mode Other Tips Sour/salty? -> Go for a ratio of 1:2.5+ (also try higher temperatures) Bitter/dry? -> Go for a ratio of 1:1.5- (also try lower temperatures) How to adapt Flow (Phase 2,3 & 4) Calculate based on an ideal shot time not recipe (brewing phase only, traditionally, one would say from the first drop in the cup). Yield / Time  Example for a 1:2 ratio: 36ml / 20s = 1.8g/s Phase 1: Pre-Infusion; water drawn Dose × 1.3 + Headspace (of your portafilter) Phase 3: Weight reached Flow × Phase 3 Duration For more, check detailed explanation Default Example 18g 91°C, 18g Dose → 36g Yield (1:2 Ratio), for Classic Espresso (Medium-Dark Roasts)  Settings Summary Yield / Ratio: Default: 1:2 (Stop at ~36g) Time Mode (No Scale): Set the last phase (Brewing) to ~14 seconds (or ~36s Total Time). The Profile Steps (at a glance) Phase Goal Flow Pressure Limit Time Stop Trigger 1. Pre-Infusion Fill headspace, moisten puck. 20 g/s 2 bar 10 s 1g (Weight) 31ml (Water drawn) 2. Bloom Saturate puck, prevent channeling. 1.8 g/s 2 bar 1–10 s 1.5g (Weight) 3. Ramp-Up Build pressure smoothly. 1.8 g/s 12 bar 6 s 11g (Weight)* 4. Brewing Main extraction. 1.8 g/s 9 bar 120 s 36g (Weight)** *Calculated safety limit (1.8g/s × 6s). **Or manual stop. 2. Detailed Explanation Phase 1: Pre-Infusion Goal: Quickly fill the headspace and moisten the puck without building pressure. First, we must pump enough water to completely saturate the coffee. We calculate this based on the dose and headspace. Calculation: Dose × 1.3 + Headspace. Example (18g basket): 18g * 1.3 + 7.5ml ≈ 31ml (Water drawn). To fill this volume quickly, we set a high flow rate (20 g/s). We allow 10 seconds for this phase, ensuring it completes even if the machine pumps slower (e.g., 4 g/s). Crucial: This must happen without pressure to avoid premature extraction. We limit the pressure to 2 bar. Safety Stop: The phase ends immediately if 1g of coffee lands in the cup. Note: The water drawn stop parameter prevents a long pre-infusion if it does not drip and enables a significantly more reliable pre-infusion in time mode. If you use a scale, you can also delete the water drawn parameter entirely if you do not find it helpful. Phase 2: Bloom Goal: Saturate the puck, prevent channeling, and wait for extraction readiness. Phase 2 ensures the puck is fully soaked. Instead of a static wait, we maintain a slight flow (1.8 g/s, matching the main brew flow) to make the bloom effective. We keep the pressure limit at 2 bar. This effectively pauses the water flow if the puck is already pressurized/saturated. Safety Stop: The phase ends immediately if 1.5g of coffee lands in the cup. Duration: 1–10 seconds. Tip: A longer bloom time allows for finer grinding. Phase 3: Ramp-Up Goal: Build pressure smoothly. We maintain the flow rate of 1.8 g/s but raise the pressure limit to 12 bar. This facilitates a smooth pressure ramp-up following pre-infusion. While 9 bar is generally the target, the higher 12-bar ceiling accommodates finer grinds that require an extra "push" to start flowing. We cap this phase at 6 seconds to prevent pressure spikes on coarser grinds Safety Stop: The phase ends immediately if 11g (6s × 1.8g/s) of coffee lands in the cup (Flow × Phase 3 Duration). Note on Profile Logic: Previously, we used increased flow to build pressure quickly, but this often caused spikes. We now use continuous flow for consistency. Comparison: The Adaptive v2 profile uses a pressure-based ramp-up to transfer flow rates (good for varying doses), while Pure Flow skips this phase entirely. Each method has its merits, so feel free to experiment. Future updates may use real-time puck flow data for an even better ramp-up. Phase 4: Brewing Goal: Main extraction at constant flow. The flow rate is crucial here. We calculate it based on an ideal shot time. (Time measurement starts for our calculations from the first drop). We can use 20 seconds for every dose at a 1:2 extraction ratio: Calculation: Dose × 2 / 20s = Flow Example: (18g basket): 18g × 2 / 20s ≈ 1.8g/s If we were to use this calculation for a hypothetical 1:2.5 extraction ratio, both the target weight and the time would be extended, usually proportionally. For us, however, this means that we can only calculate the flow based on the 1:2 extraction time. Pressure & Flavor: To prevent bitterness, we limit pressure to 9 bar. If the coffee is ground very finely, you might see a "Second Blooming" effect: The transition from the 12-bar ramp-up (Phase 3) to the 9-bar limit (Phase 4) can cause the flow to briefly pause as the system regulates. This is not an error. It often produces a rich, chocolatey profile rather than a bitter one. Recommendation: Try this with chocolatey/nutty roasts at a 1:1.5 ratio. Limits & Safety: We set a generous 120-second duration. This allows for high-resistance shots or long coffees if you grind coarser. For weight mode: Weight = Dose × Ratio Example: 18g in, 1:2 ratio: 2 × 18g = 36g For time mode: Time = (Dose × Ratio) / Flow + 16s Example: 18g in, 1:2 ratio: (2 × 18g) / 1.8g/s + 16s = 36s 16s = Duration phase 1 + Duration phase 2 Or change time directly in your profile: Last phase duration = (Dose × Ratio) / Flow - Duration phase 3` |
| :---- |